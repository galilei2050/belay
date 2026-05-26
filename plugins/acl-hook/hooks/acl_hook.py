#!/usr/bin/env python3
"""
ACL hook for Claude Code Bash commands.

Single job: decide allow / ask / deny for each Bash invocation, so the user
only sees prompts for genuinely ambiguous commands. No project knowledge, no
harness gates, no verification / review checks.

Rule match types:
  "args"         — ordered subsequence match (each pattern matches an arg in order)
  "args_contain" — any arg matches any pattern (unordered)
  "args_glob"    — full argument string matched as a single glob
"""

import gzip
import json
import logging
import os
import shlex
import sys
from fnmatch import fnmatch
from logging.handlers import RotatingFileHandler
from pathlib import Path

import bashlex

HOME = str(Path.home())
# Project root passed by Claude Code as CLAUDE_PROJECT_DIR. Fall back to cwd
# when invoked outside a Claude Code session (tests, manual runs).
PROJECT_DIR = os.path.realpath(os.environ.get("CLAUDE_PROJECT_DIR", os.getcwd()))

# fmt: off
ACL = {
    "git": {
        "rules": [
            # Hard denies — never bypass
            {"args": ["commit", "--amend"],          "decision": "deny", "reason": "Never amend commits"},
            {"args": ["push", "--force"],            "decision": "deny", "reason": "Never force push"},
            {"args": ["push", "--force-with-lease"], "decision": "deny", "reason": "Never force push"},
            {"args": ["*", "--no-verify"],           "decision": "deny", "reason": "Never skip hooks"},
            {"args": ["filter-branch"],              "decision": "deny", "reason": "filter-branch rewrites history"},
            {"args": ["filter-repo"],                "decision": "deny", "reason": "filter-repo rewrites history"},

            # Destructive subcommands — agent must not silently rewrite history
            {"args": ["reset"],       "decision": "deny", "reason": "Don't reset history. Commit forward (new commit / `git revert`) or ask the user to run `git reset` themselves."},
            {"args": ["clean", "-f"], "decision": "ask",  "reason": "Confirm git clean"},
            {"args": ["branch", "-D"], "decision": "ask", "reason": "Confirm force-delete branch"},
            {"args": ["branch", "-d"], "decision": "ask", "reason": "Confirm delete branch"},
            {"args": ["rebase"],      "decision": "deny", "reason": "Agent never rebases — commit forward or ask the user to rebase manually"},
            {"args": ["cherry-pick"], "decision": "deny", "reason": "Agent never cherry-picks — open a PR with the desired commits, or ask user"},
            {"args": ["merge"],       "decision": "deny", "reason": "Agent never merges — use PRs via `gh pr create`/merge UI"},
            {"args": ["revert"],      "decision": "ask",  "reason": "Confirm revert — legitimate way to undo a bad commit"},

            # Commit is allow (reversible on solo branch); use a separate plugin
            # (verify-gate, code-review-gate, …) if you want pre-commit gates.
            {"args": ["commit"],              "decision": "allow", "reason": ""},

            # Config: reads ok, writes confirmed
            {"args": ["config", "--get"],  "decision": "allow", "reason": ""},
            {"args": ["config", "--list"], "decision": "allow", "reason": ""},
            {"args": ["config"],           "decision": "ask",   "reason": "Confirm git config write"},

            # Allow-list: read-only + safe state-changing operations
            {"args": ["status"],     "decision": "allow", "reason": ""},
            {"args": ["log"],        "decision": "allow", "reason": ""},
            {"args": ["diff"],       "decision": "allow", "reason": ""},
            {"args": ["show"],       "decision": "allow", "reason": ""},
            {"args": ["blame"],      "decision": "allow", "reason": ""},
            {"args": ["describe"],   "decision": "allow", "reason": ""},
            {"args": ["rev-parse"],  "decision": "allow", "reason": ""},
            {"args": ["ls-files"],   "decision": "allow", "reason": ""},
            {"args": ["ls-tree"],    "decision": "allow", "reason": ""},
            {"args": ["branch"],     "decision": "allow", "reason": ""},
            {"args": ["fetch"],      "decision": "allow", "reason": ""},
            {"args": ["pull"],       "decision": "allow", "reason": ""},
            {"args": ["push"],       "decision": "allow", "reason": ""},
            {"args": ["add", "-A"],     "decision": "deny", "reason": "`git add -A` stages everything in the working tree, which sneaks in secrets (.env), build artifacts, half-finished work, and files unrelated to the current change. INSTEAD: list files by path — `git add path/to/file1 path/to/file2`. If you don't know what's changed, run `git status` first and add the relevant ones deliberately."},
            {"args": ["add", "--all"],  "decision": "deny", "reason": "`git add --all` stages everything (same problem as `-A`). INSTEAD: list files by path. Run `git status` first if you need to see what's changed."},
            {"args": ["add", "."],      "decision": "deny", "reason": "`git add .` stages everything under cwd, which sneaks in unintended files. INSTEAD: list files by path — `git add path/to/file1 path/to/file2`. Run `git status` first if you need to see what's changed."},
            {"args": ["add"],        "decision": "allow", "reason": ""},
            {"args": ["mv"],         "decision": "allow", "reason": ""},
            {"args": ["clone"],      "decision": "allow", "reason": ""},
            {"args": ["restore"],    "decision": "allow", "reason": ""},
            {"args": ["checkout"],   "decision": "allow", "reason": ""},
            {"args": ["switch"],     "decision": "allow", "reason": ""},
            {"args": ["stash"],      "decision": "allow", "reason": ""},
            {"args": ["tag"],        "decision": "allow", "reason": ""},
            {"args": ["remote"],     "decision": "allow", "reason": ""},
            {"args": ["reflog"],     "decision": "allow", "reason": ""},
            {"args": ["worktree"],   "decision": "allow", "reason": ""},
            {"args": ["merge-base"], "decision": "allow", "reason": ""},
            {"args": ["shortlog"],   "decision": "allow", "reason": ""},
            {"args": ["grep"],       "decision": "allow", "reason": ""},
            {"args": ["init"],       "decision": "ask",   "reason": "Confirm git init"},
        ],
        "default": "deny",
        "reason": "git subcommand not in allow-list. Use status/log/diff/add/commit/push/checkout/branch/restore or ask the user which command they want.",
    },
    "cat": {
        "rules": [{"args_contain": [".env*"], "decision": "deny", "reason": "Env files blocked"}],
        "default": "allow",
    },
    "head": {
        "rules": [{"args_contain": [".env*"], "decision": "deny", "reason": "Env files blocked"}],
        "default": "allow",
    },
    "tail": {
        "rules": [{"args_contain": [".env*"], "decision": "deny", "reason": "Env files blocked"}],
        "default": "allow",
    },
    "less": {
        "rules": [{"args_contain": [".env*"], "decision": "deny", "reason": "Env files blocked"}],
        "default": "allow",
    },
    "more": {
        "rules": [{"args_contain": [".env*"], "decision": "deny", "reason": "Env files blocked"}],
        "default": "allow",
    },
    "source":  {"rules": [], "default": "deny", "reason": "source is blocked. If the venv isn't active, ask the user to activate it — do not try workarounds."},
    ".":       {"rules": [], "default": "deny", "reason": "`.` (source builtin) is blocked — same as `source`."},
    "env":     {"rules": [], "default": "deny", "reason": "env is blocked — bypasses ACL via leading env-var assignments. Read env vars from inside your program, or ask the user."},
    "xargs":   {"rules": [], "default": "deny", "reason": "xargs bypasses ACL. Use a `for` loop or run commands directly."},
    "python3": {"rules": [], "default": "allow"},
    "python":  {"rules": [], "default": "allow"},
    "gh": {
        "rules": [
            {"args": ["pr", "view"],    "decision": "allow", "reason": ""},
            {"args": ["pr", "list"],    "decision": "allow", "reason": ""},
            {"args": ["pr", "checks"],  "decision": "allow", "reason": ""},
            {"args": ["pr", "diff"],    "decision": "allow", "reason": ""},
            {"args": ["pr", "status"],  "decision": "allow", "reason": ""},
            {"args": ["pr", "comment"], "decision": "ask",   "reason": "Confirm before posting PR comment (outward-facing)"},
            {"args": ["pr", "edit"],    "decision": "allow", "reason": ""},
            {"args": ["pr", "ready"],   "decision": "allow", "reason": ""},
            {"args": ["pr", "create"],  "decision": "allow", "reason": ""},

            {"args": ["repo", "view"],    "decision": "allow", "reason": ""},
            {"args": ["repo", "list"],    "decision": "allow", "reason": ""},
            {"args": ["run", "view"],     "decision": "allow", "reason": ""},
            {"args": ["run", "list"],     "decision": "allow", "reason": ""},
            {"args": ["run", "watch"],    "decision": "allow", "reason": ""},
            {"args": ["api"],             "decision": "allow", "reason": ""},
            {"args": ["auth", "status"],  "decision": "allow", "reason": ""},
            {"args": ["issue", "view"],   "decision": "allow", "reason": ""},
            {"args": ["issue", "list"],   "decision": "allow", "reason": ""},
            {"args": ["issue", "comment"],"decision": "ask",   "reason": "Confirm before posting issue comment (outward-facing)"},
            {"args": ["issue", "create"], "decision": "ask",   "reason": "Confirm before creating issue"},
            {"args": ["workflow", "view"],"decision": "allow", "reason": ""},
            {"args": ["workflow", "list"],"decision": "allow", "reason": ""},
            {"args": ["release", "view"], "decision": "allow", "reason": ""},
            {"args": ["release", "list"], "decision": "allow", "reason": ""},
            {"args": ["secret", "list"],  "decision": "allow", "reason": ""},
        ],
        "default": "ask",
        "reason": "gh subcommand not in allow-list — confirm. Merge/close/delete/release are user-only.",
    },
    "rm": {
        "rules": [
            {"args_contain": [".env*"], "decision": "deny", "reason": "Env files blocked"},
            {"fn": "all_paths_inside_project", "decision": "ask", "reason": "Confirm rm inside project tree"},
        ],
        "default": "deny",
        "reason": "rm only allowed inside the project tree — system paths off-limits",
    },
    "nc":      {"rules": [], "default": "ask",  "reason": "Confirm before using nc"},
    "pip":     {"rules": [{"args": ["install"], "decision": "ask", "reason": "Confirm before installing packages"}], "default": "allow"},
    "cp":  {
        "rules": [{"args_contain": [".env*"], "decision": "deny", "reason": "Env files blocked"}],
        "default": "allow",
    },
    "mv":  {
        "rules": [{"args_contain": [".env*"], "decision": "deny", "reason": "Env files blocked"}],
        "default": "allow",
    },
    "grep": {
        "rules": [{"args_contain": [".env*"], "decision": "deny", "reason": "Env files blocked"}],
        "default": "allow",
    },
    "rg": {
        "rules": [{"args_contain": [".env*"], "decision": "deny", "reason": "Env files blocked"}],
        "default": "allow",
    },
    "sed": {
        "rules": [
            {"args_contain": [".env*"], "decision": "deny", "reason": "Env files blocked"},
            {"fn": "sed_inline_long", "decision": "deny", "reason": "`sed -i` expression too long (>300 chars) — use the Edit tool instead. Edit shows a diff, doesn't risk regex mishaps, and the change is reviewable. If you genuinely need a long regex replacement, split it into multiple short `sed -e` expressions or use Edit with the new content."},
        ],
        "default": "allow",
    },
    "diff": {
        "rules": [{"args_contain": [".env*"], "decision": "deny", "reason": "Env files blocked"}],
        "default": "allow",
    },
    "awk": {
        "rules": [{"args_contain": [".env*"], "decision": "deny", "reason": "Env files blocked"}],
        "default": "allow",
    },
    "tee": {
        "rules": [{"args_contain": [".env*"], "decision": "deny", "reason": "Env files blocked"}],
        "default": "allow",
    },
    "curl": {
        "rules": [
            {"args_contain": ["@.env*", "-d*@.env*"], "decision": "deny", "reason": "Env file exfiltration blocked"},
            {"fn": "curl_mutating_remote", "decision": "ask", "reason": "Confirm POST/PUT/PATCH/DELETE to remote"},
        ],
        "default": "allow",
    },
    "echo":    {"rules": [], "default": "allow"},
    "printf":  {"rules": [], "default": "allow"},
    "ls":      {"rules": [], "default": "allow"},
    "lsof":    {"rules": [], "default": "allow"},
    "kill":    {"rules": [], "default": "allow"},
    "pkill":   {"rules": [], "default": "allow"},
    "pwd":     {"rules": [], "default": "allow"},
    "tr":      {"rules": [], "default": "allow"},
    "until":   {"rules": [], "default": "allow"},
    "export":  {"rules": [], "default": "allow"},
    "unset":   {"rules": [], "default": "allow"},
    "getent":  {"rules": [], "default": "allow"},
    "ip":      {"rules": [], "default": "allow"},
    "chmod":   {"rules": [], "default": "allow"},
    "ps":      {"rules": [], "default": "allow"},
    "pyright":  {"rules": [], "default": "allow"},
    "ruff":     {"rules": [], "default": "allow"},
    "mypy":     {"rules": [], "default": "allow"},
    "isort":    {"rules": [], "default": "allow"},
    "black":    {"rules": [], "default": "allow"},
    "flake8":   {"rules": [], "default": "allow"},
    "pylint":   {"rules": [], "default": "allow"},
    "cd":      {"rules": [], "default": "allow"},
    "mkdir":   {"rules": [], "default": "allow"},
    "tree":    {"rules": [], "default": "allow"},
    "test":    {"rules": [], "default": "allow"},
    "touch":   {"rules": [], "default": "allow"},
    "date":    {"rules": [], "default": "allow"},
    "whoami":  {"rules": [], "default": "allow"},
    "which":   {"rules": [], "default": "allow"},
    "jq":      {"rules": [], "default": "allow"},
    "stat":    {"rules": [], "default": "allow"},
    "wc":      {"rules": [], "default": "allow"},
    "true":    {"rules": [], "default": "allow"},
    "find": {
        "rules": [{"args_contain": [".env*"], "decision": "deny", "reason": "Env files blocked"}],
        "default": "allow",
    },
    "cut": {
        "rules": [{"args_contain": [".env*"], "decision": "deny", "reason": "Env files blocked"}],
        "default": "allow",
    },
    "iconv":   {"rules": [], "default": "allow"},
    "id":      {"rules": [], "default": "allow"},
    "systemctl": {
        "rules": [
            {"args": ["status"],     "decision": "allow", "reason": ""},
            {"args": ["is-active"],  "decision": "allow", "reason": ""},
            {"args": ["is-enabled"], "decision": "allow", "reason": ""},
            {"args": ["list-units"], "decision": "allow", "reason": ""},
            {"args": ["show"],       "decision": "allow", "reason": ""},
            {"args": ["cat"],        "decision": "allow", "reason": ""},
        ],
        "default": "ask",
        "reason": "systemctl mutations (start/stop/enable/disable/reload) need confirmation.",
    },
    "make":    {"rules": [], "default": "allow"},
    "docker": {
        "rules": [
            {"args": ["ps"],      "decision": "allow", "reason": ""},
            {"args": ["images"],  "decision": "allow", "reason": ""},
            {"args": ["logs"],    "decision": "allow", "reason": ""},
            {"args": ["inspect"], "decision": "allow", "reason": ""},
            {"args": ["info"],    "decision": "allow", "reason": ""},
            {"args": ["version"], "decision": "allow", "reason": ""},
            {"args": ["stats"],   "decision": "allow", "reason": ""},
            {"args": ["history"], "decision": "allow", "reason": ""},
            {"args": ["top"],     "decision": "allow", "reason": ""},
            {"args": ["port"],    "decision": "allow", "reason": ""},
            {"args": ["events"],  "decision": "allow", "reason": ""},
            {"args": ["diff"],    "decision": "allow", "reason": ""},
            {"args": ["pull"],    "decision": "allow", "reason": ""},
            {"args": ["build"],   "decision": "allow", "reason": ""},
            {"args": ["push"],    "decision": "ask",   "reason": "Confirm docker push (outward-facing)"},
            {"args": ["run"],     "decision": "allow", "reason": ""},
            {"args": ["exec"],    "decision": "allow", "reason": ""},
            {"args": ["start"],   "decision": "allow", "reason": ""},
            {"args": ["stop"],    "decision": "allow", "reason": ""},
            {"args": ["restart"], "decision": "allow", "reason": ""},
            {"args": ["rm"],      "decision": "ask",   "reason": "Confirm docker rm"},
            {"args": ["rmi"],     "decision": "ask",   "reason": "Confirm docker rmi"},
            {"args": ["prune"],   "decision": "ask",   "reason": "Confirm docker prune"},
            {"args": ["compose"], "decision": "ask",   "reason": "Confirm docker compose"},
            {"args": ["login"],   "decision": "deny",  "reason": "No registry login from agent"},
            {"args": ["logout"],  "decision": "deny",  "reason": "No registry logout from agent"},
        ],
        "default": "ask",
        "reason": "docker subcommand not in allow-list — confirm.",
    },
    "fuser":   {"rules": [], "default": "allow"},
    "paste":   {"rules": [], "default": "allow"},
    "pre-commit": {"rules": [], "default": "allow"},
    "journalctl": {"rules": [], "default": "allow"},
    "du":         {"rules": [], "default": "allow"},
    "file":       {"rules": [], "default": "allow"},
    "df":         {"rules": [], "default": "allow"},
    "lsblk":      {"rules": [], "default": "allow"},
    "findmnt":    {"rules": [], "default": "allow"},
    "rmdir": {
        "rules": [{"fn": "all_paths_inside_project", "decision": "ask", "reason": "Confirm rmdir inside project tree"}],
        "default": "deny",
        "reason": "rmdir only allowed inside the project tree",
    },
    "pytest":     {"rules": [], "default": "allow"},
    "streamlit":  {"rules": [], "default": "allow"},
    "pulumi": {
        "rules": [
            {"args": ["preview"],         "decision": "allow", "reason": ""},
            {"args": ["stack", "ls"],     "decision": "allow", "reason": ""},
            {"args": ["stack", "select"], "decision": "allow", "reason": ""},
            {"args": ["config", "get"],   "decision": "allow", "reason": ""},
        ],
        "default": "ask",
        "reason": "pulumi mutations (up/destroy) need confirmation.",
    },
    "pgrep":   {"rules": [], "default": "allow"},
    "set":     {"rules": [], "default": "allow"},
    "sleep":   {"rules": [], "default": "allow"},
    "sort":    {"rules": [], "default": "allow"},
    "uniq":    {"rules": [], "default": "allow"},
    "if":      {"rules": [], "default": "allow"},
    "then":    {"rules": [], "default": "allow"},
    "else":    {"rules": [], "default": "allow"},
    "elif":    {"rules": [], "default": "allow"},
    "fi":      {"rules": [], "default": "allow"},
    "[":       {"rules": [], "default": "allow"},
    "[[":      {"rules": [], "default": "allow"},
    "for":     {"rules": [], "default": "allow"},
    "do":      {"rules": [], "default": "allow"},
    "done":    {"rules": [], "default": "allow"},
    "while":   {"rules": [], "default": "allow"},
    "npm": {
        "rules": [
            {"args": ["list"],      "decision": "allow", "reason": ""},
            {"args": ["ls"],        "decision": "allow", "reason": ""},
            {"args": ["view"],      "decision": "allow", "reason": ""},
            {"args": ["info"],      "decision": "allow", "reason": ""},
            {"args": ["outdated"],  "decision": "allow", "reason": ""},
            {"args": ["audit"],     "decision": "allow", "reason": ""},
            {"args": ["test"],      "decision": "allow", "reason": ""},
            {"args": ["run"],       "decision": "allow", "reason": ""},
            {"args": ["start"],     "decision": "allow", "reason": ""},
            {"args": ["install"],   "decision": "ask",   "reason": "Confirm npm install"},
            {"args": ["i"],         "decision": "ask",   "reason": "Confirm npm install"},
            {"args": ["ci"],        "decision": "allow", "reason": ""},
            {"args": ["uninstall"], "decision": "ask",   "reason": "Confirm npm uninstall"},
            {"args": ["update"],    "decision": "ask",   "reason": "Confirm npm update"},
            {"args": ["publish"],   "decision": "deny",  "reason": "Never publish from agent"},
            {"args": ["adduser"],   "decision": "deny",  "reason": "No npm auth changes"},
            {"args": ["login"],     "decision": "deny",  "reason": "No npm auth changes"},
            {"args": ["token"],     "decision": "deny",  "reason": "No npm tokens"},
        ],
        "default": "ask",
        "reason": "npm subcommand not in allow-list — confirm.",
    },
    "npx":     {"rules": [], "default": "ask",  "reason": "Confirm npx — runs arbitrary package"},
    "gunzip":  {"rules": [], "default": "allow"},
    "zcat":    {"rules": [], "default": "allow"},
    "bash":    {"rules": [], "default": "deny", "reason": "Use the Bash tool directly or chain commands with && instead"},
    "sh":      {"rules": [], "default": "deny", "reason": "Use the Bash tool directly or chain commands with && instead"},
    "dash":    {"rules": [], "default": "deny", "reason": "Use the Bash tool directly or chain commands with && instead"},
    "zsh":     {"rules": [], "default": "deny", "reason": "Use the Bash tool directly or chain commands with && instead"},
    "ksh":     {"rules": [], "default": "deny", "reason": "Use the Bash tool directly or chain commands with && instead"},
    "fish":    {"rules": [], "default": "deny", "reason": "Use the Bash tool directly or chain commands with && instead"},
    "command": {"rules": [], "default": "deny", "reason": "`command <X>` is the bash `command` builtin and it bypasses ACL. Do NOT prefix anything with `command` — write the bare command. Example: instead of `command git status`, write `git status`."},
    "eval":    {"rules": [], "default": "deny", "reason": "`eval` bypasses ACL by executing a constructed string. Run the command directly."},
    "exec":    {"rules": [], "default": "deny", "reason": "`exec` bypasses ACL by replacing the shell. Run the command directly."},
    "builtin": {"rules": [], "default": "deny", "reason": "`builtin` bypasses ACL. Run the command directly."},
    "sudo":    {"rules": [], "default": "deny", "reason": "No privilege escalation from agent."},
    "pkexec":  {"rules": [], "default": "deny", "reason": "No privilege escalation from agent."},
    "doas":    {"rules": [], "default": "deny", "reason": "No privilege escalation from agent."},
    "ffmpeg":  {"rules": [], "default": "allow"},
    "ffprobe": {"rules": [], "default": "allow"},
    "gcloud": {
        "rules": [
            {"args_contain": ["set-iam-policy", "add-iam-policy-binding", "remove-iam-policy-binding"],
             "decision": "deny", "reason": "IAM changes blocked"},
            {"args": ["auth", "activate-service-account"], "decision": "deny", "reason": "Service account impersonation blocked"},

            {"args_contain": ["print-access-token", "print-identity-token"],
             "decision": "ask", "reason": "Confirm auth token output"},
            {"args": ["secrets", "versions", "access"],
             "decision": "ask", "reason": "Confirm secret access"},

            {"args_contain": ["deploy"], "decision": "ask", "reason": "Confirm before deploying"},
            {"args_contain": ["delete"], "decision": "ask", "reason": "Confirm before deleting"},
            {"args_contain": ["create"], "decision": "ask", "reason": "Confirm before creating resources"},
            {"args_contain": ["update"], "decision": "ask", "reason": "Confirm before updating resources"},
            {"args_contain": ["import"], "decision": "ask", "reason": "Confirm before importing resources"},
            {"args": ["services", "disable"], "decision": "ask", "reason": "Confirm before disabling services"},
            {"args": ["services", "enable"],  "decision": "ask", "reason": "Confirm before enabling services"},
            {"args": ["config", "set"],       "decision": "ask", "reason": "Confirm gcloud config write"},
            {"args": ["config", "unset"],     "decision": "ask", "reason": "Confirm gcloud config write"},
            {"args": ["auth", "login"],       "decision": "deny", "reason": "User runs `gcloud auth login` interactively themselves — agent can't complete the browser flow"},
            {"args": ["auth", "revoke"],      "decision": "deny", "reason": "Auth changes are user-only"},

            {"args": ["storage", "ls"],          "decision": "allow", "reason": ""},
            {"args": ["storage", "cat"],         "decision": "allow", "reason": ""},
            {"args": ["pubsub", "subscriptions", "pull"], "decision": "allow", "reason": ""},
            {"args_contain": ["list"],           "decision": "allow", "reason": ""},
            {"args_contain": ["describe"],       "decision": "allow", "reason": ""},
            {"args_contain": ["info"],           "decision": "allow", "reason": ""},
            {"args_contain": ["read"],           "decision": "allow", "reason": ""},
            {"args_contain": ["show"],           "decision": "allow", "reason": ""},
            {"args_contain": ["check"],          "decision": "allow", "reason": ""},
            {"args_contain": ["lookup"],         "decision": "allow", "reason": ""},
            {"args_contain": ["query"],          "decision": "allow", "reason": ""},
            {"args_contain": ["status"],         "decision": "allow", "reason": ""},
            {"args_contain": ["get-*"],          "decision": "allow", "reason": ""},
            {"args_contain": ["print-settings"], "decision": "allow", "reason": ""},
            {"args_contain": ["log"],            "decision": "allow", "reason": ""},
            {"args_contain": ["logs"],           "decision": "allow", "reason": ""},
            {"args_contain": ["tail"],           "decision": "allow", "reason": ""},
            {"args": ["version"],                "decision": "allow", "reason": ""},
            {"args": ["help"],                   "decision": "allow", "reason": ""},
        ],
        "default": "ask",
        "reason": "gcloud subcommand not in read allow-list — confirm.",
    },
}
# fmt: on

DECISION_PRIORITY = {"deny": 2, "ask": 1, "allow": 0}


def _gz_namer(name):
    return name + ".gz"


def _gz_rotator(source, dest):
    with open(source, "rb") as f_in:
        with gzip.open(dest, "wb") as f_out:
            f_out.write(f_in.read())
    os.remove(source)


def setup_logging():
    log_dir = os.path.join(HOME, ".claude", "logs")
    os.makedirs(log_dir, exist_ok=True)
    logger = logging.getLogger("acl_hook")
    logger.setLevel(logging.INFO)
    handler = RotatingFileHandler(os.path.join(log_dir, "acl-hook.log"), maxBytes=5_000_000, backupCount=5)
    handler.namer = _gz_namer
    handler.rotator = _gz_rotator
    handler.setFormatter(logging.Formatter("[%(asctime)s] %(message)s", datefmt="%Y-%m-%d %H:%M:%S"))
    logger.addHandler(handler)
    return logger


def expand_home(arg):
    if arg == "~":
        return HOME
    if arg.startswith("~/"):
        return HOME + arg[1:]
    return arg


def split_chained_commands(command):
    """Split command on &&, ;, | respecting quotes."""
    parts = []
    current = []
    in_single = False
    in_double = False
    i = 0
    while i < len(command):
        c = command[i]
        if c == "'" and not in_double:
            in_single = not in_single
            current.append(c)
        elif c == '"' and not in_single:
            in_double = not in_double
            current.append(c)
        elif not in_single and not in_double:
            if c == "|" or c == ";":
                parts.append("".join(current).strip())
                current = []
            elif c == "&" and i + 1 < len(command) and command[i + 1] == "&":
                parts.append("".join(current).strip())
                current = []
                i += 1
            else:
                current.append(c)
        else:
            current.append(c)
        i += 1
    parts.append("".join(current).strip())
    return [p for p in parts if p]


def arg_matches(arg, pattern):
    return fnmatch(arg, pattern) or fnmatch(os.path.basename(arg), pattern)


def matches_args(rule_patterns, cmd_args):
    cmd_idx = 0
    for pattern in rule_patterns:
        found = False
        while cmd_idx < len(cmd_args):
            if arg_matches(cmd_args[cmd_idx], pattern):
                cmd_idx += 1
                found = True
                break
            cmd_idx += 1
        if not found:
            return False
    return True


def matches_args_contain(rule_patterns, cmd_args):
    for pattern in rule_patterns:
        for arg in cmd_args:
            if arg_matches(arg, pattern):
                return True
    return False


def matches_args_glob(glob_pattern, cmd_args):
    arg_str = " ".join(cmd_args)
    return fnmatch(arg_str, glob_pattern)


LOCALHOST = ("localhost", "127.0.0.1", "0.0.0.0", "[::1]")
MUTATING_METHODS = {"POST", "PUT", "PATCH", "DELETE"}


def curl_mutating_remote(args):
    """True if curl uses a mutating method against a non-localhost target."""
    mutating = False
    for i, arg in enumerate(args):
        if arg in ("-X", "--request") and i + 1 < len(args) and args[i + 1].upper() in MUTATING_METHODS:
            mutating = True
        if arg.startswith("-X") and len(arg) > 2 and arg[2:].upper() in MUTATING_METHODS:
            mutating = True
        if arg in ("-d", "--data", "--data-raw", "--data-binary", "--data-urlencode"):
            mutating = True
    if not mutating:
        return False
    for arg in args:
        if not arg.startswith("-") and any(h in arg for h in LOCALHOST):
            return False
    return True


# Standalone `python -c "…"` is denied at top level in main() — see python_c_not_after_pipe.
MAX_BASH_LEN = 1500
MAX_BASH_LINES = 10
SED_INLINE_EXPR_MAX = 300


def rm_recursive(args):
    for arg in args:
        if arg == "--recursive":
            return True
        if arg.startswith("-") and not arg.startswith("--") and ("r" in arg or "R" in arg):
            return True
    return False


def all_paths_inside_project(args):
    """True iff every non-flag path arg resolves inside PROJECT_DIR (and at least one exists)."""
    has_path = False
    for arg in args:
        if arg.startswith("-"):
            continue
        has_path = True
        path = arg if os.path.isabs(arg) else os.path.join(PROJECT_DIR, arg)
        real = os.path.realpath(path)
        if not real.startswith(PROJECT_DIR + os.sep) and real != PROJECT_DIR:
            return False
    return has_path


def sed_inline_long(args):
    if "-i" not in args and not any(a.startswith("-i") for a in args):
        return False
    for arg in args:
        if arg.startswith("-"):
            continue
        if any(tok in arg for tok in ("s|", "s/", "s#", "s@")):
            return len(arg) > SED_INLINE_EXPR_MAX
    return False


CUSTOM_FNS = {
    "curl_mutating_remote": curl_mutating_remote,
    "sed_inline_long": sed_inline_long,
    "rm_recursive": rm_recursive,
    "all_paths_inside_project": all_paths_inside_project,
}


def check_rule(rule, cmd_args):
    if "fn" in rule:
        return CUSTOM_FNS[rule["fn"]](cmd_args)
    if "args" in rule:
        return matches_args(rule["args"], cmd_args)
    if "args_contain" in rule:
        return matches_args_contain(rule["args_contain"], cmd_args)
    if "args_glob" in rule:
        return matches_args_glob(rule["args_glob"], cmd_args)
    return False


# --- Top-level antipattern detectors ---


def _walk_with_parent(node, parent=None, position=None):
    yield node, parent, position
    children = list(_node_children(node))
    for idx, child in enumerate(children):
        yield from _walk_with_parent(child, parent=node, position=idx)


def _node_children(node):
    parts = getattr(node, "parts", None)
    if parts:
        yield from parts
    list_children = getattr(node, "list", None)
    if list_children:
        yield from list_children
    cmd_child = getattr(node, "command", None)
    if cmd_child is not None:
        yield cmd_child


def _command_words(node):
    if getattr(node, "kind", None) != "command":
        return []
    words = []
    for part in getattr(node, "parts", []) or []:
        if getattr(part, "kind", None) == "word":
            words.append(part.word)
    return words


def has_function_def(trees) -> bool:
    """True iff any tree contains a Bash function definition."""
    for tree in trees:
        for node, _parent, _pos in _walk_with_parent(tree):
            if getattr(node, "kind", None) == "function":
                return True
    return False


def python_c_not_after_pipe(trees) -> bool:
    """True iff any `python[3] -c …` invocation is NOT positioned as a pipe receiver."""
    for tree in trees:
        for node, parent, position in _walk_with_parent(tree):
            words = _command_words(node)
            if not words:
                continue
            basename = os.path.basename(words[0])
            if basename not in ("python", "python3"):
                continue
            if "-c" not in words[1:]:
                continue
            if parent is not None and getattr(parent, "kind", None) == "pipeline" and (position or 0) > 0:
                continue
            return True
    return False


def until_loop_with_sleep(trees) -> bool:
    """True iff a Bash invocation contains both `until` (reserved word) and a `sleep` command."""
    for tree in trees:
        has_until = False
        has_sleep = False
        for node, _parent, _pos in _walk_with_parent(tree):
            if getattr(node, "kind", None) == "reservedword" and getattr(node, "word", "") == "until":
                has_until = True
            words = _command_words(node)
            if words and os.path.basename(words[0]) == "sleep":
                has_sleep = True
            if has_until and has_sleep:
                return True
    return False


def chained_sleep(trees) -> bool:
    """True iff `sleep N` is chained with another command at the same nesting level."""
    for tree in trees:
        for node, parent, _pos in _walk_with_parent(tree):
            words = _command_words(node)
            if not words or os.path.basename(words[0]) != "sleep":
                continue
            if parent is None:
                continue
            siblings = [c for c in _node_children(parent) if c is not node and getattr(c, "kind", None) == "command"]
            if siblings:
                return True
    return False


def check_command(cmd_str, logger, *, agent_type: str):
    """Check a single command against ACL. Returns (decision, reason, log_detail)."""
    try:
        parts = shlex.split(cmd_str)
    except ValueError as e:
        reason = (
            f"Bash command failed to parse ({e}). Rewrite as a simpler primitive the shell "
            "can parse cleanly, or split into multiple Bash calls."
        )
        logger.info('decision=deny command="%s" matched=shlex_error agent=%s', cmd_str[:120], agent_type)
        return "deny", reason, "shlex_error"

    if not parts:
        return "allow", "", "empty command"

    # Skip leading VAR=value environment variable assignments
    while parts and "=" in parts[0] and not parts[0].startswith("-"):
        parts = parts[1:]

    # Strip leading process-wrapper commands
    while parts and parts[0] in ("time", "nohup", "nice", "setsid", "stdbuf", "ionice", "taskset"):
        parts = parts[1:]
        while parts and parts[0].startswith("-"):
            if parts[0] in ("-n", "-c", "-p") and len(parts) > 1:
                parts = parts[2:]
            else:
                parts = parts[1:]

    # Strip leading `timeout [opts] <duration>` wrapper
    if parts and parts[0] == "timeout":
        parts = parts[1:]
        while parts and parts[0].startswith("-"):
            if parts[0] in ("-s", "--signal", "-k", "--kill-after") and len(parts) > 1:
                parts = parts[2:]
            else:
                parts = parts[1:]
        if parts:
            parts = parts[1:]

    if not parts:
        return "allow", "", "empty command"

    command = parts[0]

    if command.startswith("#"):
        return "allow", "", "comment"
    args = [expand_home(a) for a in parts[1:]]

    if (
        fnmatch(command, ".claude/skills/*/*.py")
        or fnmatch(command, "*/.claude/skills/*/*.py")
        or fnmatch(command, "*/.claude/hooks/*.py")
    ):
        logger.info('decision=allow command="%s" matched=claude_script agent=%s', cmd_str, agent_type)
        return "allow", "", "claude_script"

    # Block direct paths into the project venv — use bare command names instead
    if "/" in command:
        abs_command = os.path.abspath(command if os.path.isabs(command) else os.path.join(PROJECT_DIR, command))
        venv_bin_prefix = os.path.join(PROJECT_DIR, ".venv", "bin") + os.sep
        if abs_command.startswith(venv_bin_prefix):
            bare = os.path.basename(abs_command)
            reason = (
                f"Don't invoke `{command}` — call `{bare}` directly. The project venv should be active in the shell profile.\n"
                f"If `{bare}` still fails, ASK THE USER to activate the venv (`source .venv/bin/activate` in their terminal). "
                f"Workarounds like `source`, `.`, `bash -c`, invoking the venv binary by path — all blocked."
            )
            logger.info('decision=deny command="%s" matched=venv_bin agent=%s', cmd_str, agent_type)
            return "deny", reason, "venv_bin"

    basename = os.path.basename(command)
    if "/" in command and basename in ("python", "python3"):
        reason = (
            "Use python3 directly, not a path. The project venv should be active in the shell profile.\n"
            "If `python3` runs from /usr/bin (venv not active), ASK THE USER to activate it."
        )
        logger.info('decision=deny command="%s" matched=python_path agent=%s', cmd_str, agent_type)
        return "deny", reason, "python_path"

    # Basename normalization so /usr/bin/git is ACL'd the same as bare git
    if "/" in command:
        bn = os.path.basename(command)
        if bn in ACL:
            command = bn

    if command not in ACL:
        reason = (
            f"Unknown command `{command}` — not in ACL. Don't smuggle it through a wrapper "
            "or a clever one-liner. Use a simpler primitive that IS in the allow-list "
            "(ls/cat/grep/find/git/gh/…), or split into multiple Bash calls. If you "
            "genuinely need this command, ask the user to add it to ACL."
        )
        logger.info('decision=deny command="%s" matched=unknown_command agent=%s', cmd_str, agent_type)
        return "deny", reason, "unknown_command"

    entry = ACL[command]
    for rule in entry["rules"]:
        if check_rule(rule, args):
            logger.info(
                'decision=%s command="%s" matched=rule:%s agent=%s',
                rule["decision"],
                cmd_str,
                rule.get("args") or rule.get("args_contain") or rule.get("args_glob") or rule.get("fn"),
                agent_type,
            )
            return rule["decision"], rule["reason"], "rule"

    default = entry["default"]
    reason = entry.get("reason", "")
    logger.info('decision=%s command="%s" matched=default:%s agent=%s', default, cmd_str, default, agent_type)
    return default, reason, f"default:{default}"


def main():
    data = json.loads(sys.stdin.read())

    if data.get("tool_name") != "Bash":
        sys.exit(0)

    command = data.get("tool_input", {}).get("command", "")
    if not command:
        sys.exit(0)

    agent_id = data.get("agent_id")
    agent_type = data.get("agent_type") if agent_id is not None else "main"

    logger = setup_logging()

    # Long multi-line bash blobs are the wrong tool — split into multiple simple calls.
    line_count = command.count("\n") + 1
    if len(command) > MAX_BASH_LEN or line_count > MAX_BASH_LINES:
        logger.info("decision=deny command_too_long len=%d lines=%d agent=%s", len(command), line_count, agent_type)
        print(
            json.dumps(
                {
                    "hookSpecificOutput": {
                        "hookEventName": "PreToolUse",
                        "permissionDecision": "deny",
                        "permissionDecisionReason": (
                            f"Bash command too large ({len(command)} chars / {line_count} lines; "
                            f"limit {MAX_BASH_LEN}/{MAX_BASH_LINES}). SPLIT into several simple Bash "
                            "calls — each step gets its own ACL check and feedback. Antipatterns to "
                            "avoid: long `for x in …; do …; done`, function defs `name() {…}`, `&&` chains "
                            'longer than 3 links, `python -c "<multiline script>"`. Genuinely atomic '
                            "script with control flow (rare) → Write tool to a file, then run it."
                        ),
                    }
                }
            )
        )
        return

    # Agents must use Write tool for file creation — heredoc content breaks ACL parsing
    if "<<" in command:
        logger.info('decision=deny command="%s" matched=agent_heredoc agent=%s', command[:120], agent_type)
        print(
            json.dumps(
                {
                    "hookSpecificOutput": {
                        "hookEventName": "PreToolUse",
                        "permissionDecision": "deny",
                        "permissionDecisionReason": "Agents cannot use heredoc (<<) in Bash — use the Write tool instead.",
                    }
                }
            )
        )
        return

    # Parse bash once; deny if bashlex chokes (fail-closed for AST detectors below).
    try:
        trees = bashlex.parse(command)
    except Exception as e:
        logger.info(
            'decision=deny command="%s" matched=bashlex_parse_failed agent=%s err=%s',
            command[:120],
            agent_type,
            type(e).__name__,
        )
        print(
            json.dumps(
                {
                    "hookSpecificOutput": {
                        "hookEventName": "PreToolUse",
                        "permissionDecision": "deny",
                        "permissionDecisionReason": (
                            f"Bash command failed to parse via bashlex ({type(e).__name__}): {e}. "
                            "This blocks the AST-based antipattern detectors from checking it, so we "
                            "fail closed. Likely cause: ANSI-C escapes (`$'…'`), process substitution "
                            "(`<(…)` / `>(…)`), unbalanced quotes. Rewrite as a simpler primitive or "
                            "split into multiple Bash calls."
                        ),
                    }
                }
            )
        )
        return

    if has_function_def(trees):
        logger.info('decision=deny command="%s" matched=function_def agent=%s', command[:120], agent_type)
        print(
            json.dumps(
                {
                    "hookSpecificOutput": {
                        "hookEventName": "PreToolUse",
                        "permissionDecision": "deny",
                        "permissionDecisionReason": (
                            "Bash function definitions (`name() { … }`) inside a Bash call are denied — "
                            "split into multiple simple Bash calls. If you need reusable logic, Write it "
                            "as a script file."
                        ),
                    }
                }
            )
        )
        return

    if python_c_not_after_pipe(trees):
        logger.info('decision=deny command="%s" matched=python_c_standalone agent=%s', command[:120], agent_type)
        print(
            json.dumps(
                {
                    "hookSpecificOutput": {
                        "hookEventName": "PreToolUse",
                        "permissionDecision": "deny",
                        "permissionDecisionReason": (
                            '`python -c` is allowed only as a pipe filter (`<cmd> | python3 -c "…"`). '
                            "Standalone or `$(python -c …)` is a script masquerading as a command. "
                            "Options: (1) pipe data in; (2) Write the script to a file and run it; "
                            "(3) split into simple Bash builtins or `jq`."
                        ),
                    }
                }
            )
        )
        return

    if until_loop_with_sleep(trees):
        logger.info('decision=deny command="%s" matched=until_loop_with_sleep agent=%s', command[:120], agent_type)
        print(
            json.dumps(
                {
                    "hookSpecificOutput": {
                        "hookEventName": "PreToolUse",
                        "permissionDecision": "deny",
                        "permissionDecisionReason": (
                            "`until <cond>; do … sleep N … ; done` inline polling is denied — the loop "
                            "blocks the agent for the whole wait, can't be interrupted cleanly. "
                            "Use instead: `Bash(..., run_in_background=true)` + `Monitor`/`BashOutput`; "
                            "or `ScheduleWakeup(delaySeconds=…)` in a /loop session; "
                            "or `/schedule` (CronCreate) for recurring runs."
                        ),
                    }
                }
            )
        )
        return

    if chained_sleep(trees):
        logger.info('decision=deny command="%s" matched=chained_sleep agent=%s', command[:120], agent_type)
        print(
            json.dumps(
                {
                    "hookSpecificOutput": {
                        "hookEventName": "PreToolUse",
                        "permissionDecision": "deny",
                        "permissionDecisionReason": (
                            "`sleep N` chained with another command is denied. "
                            "Use one of: (1) `Bash(..., run_in_background=true)` + `Monitor`/`BashOutput`; "
                            "(2) `ScheduleWakeup` in a /loop session; (3) `/schedule` for a cron remote agent."
                        ),
                    }
                }
            )
        )
        return

    sub_commands = split_chained_commands(command)

    final_decision = "allow"
    final_reason = ""

    for sub_cmd in sub_commands:
        decision, reason, _ = check_command(sub_cmd, logger, agent_type=agent_type)
        if DECISION_PRIORITY[decision] > DECISION_PRIORITY[final_decision]:
            final_decision = decision
            final_reason = reason

    print(
        json.dumps(
            {
                "hookSpecificOutput": {
                    "hookEventName": "PreToolUse",
                    "permissionDecision": final_decision,
                    "permissionDecisionReason": final_reason,
                }
            }
        )
    )


if __name__ == "__main__":
    main()
