#!/usr/bin/env python3
"""
ACL hook for Claude Code Bash commands.
Consolidates permission checks into a single hook with pattern-based rules.

Rule match types:
  "args"         — ordered subsequence match (each pattern matches an arg in order, not necessarily consecutive)
  "args_contain" — any arg matches any pattern (unordered, any position)
  "args_glob"    — full argument string matched as a single glob pattern
"""

import gzip
import json
import logging
import os
import re
import shlex
import subprocess
import sys
from fnmatch import fnmatch
from logging.handlers import RotatingFileHandler
from pathlib import Path

import bashlex
from git import InvalidGitRepositoryError, Repo

sys.path.insert(0, str(Path(__file__).parent))
from hook_utils import (find_work_dir, get_artifact_path,
                        staged_has_code_changes)

HOME = str(Path.home())
PROJECT_DIR = os.path.realpath(os.path.join(os.path.dirname(__file__), "..", ".."))

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
            {"args": ["rebase"],      "decision": "deny", "reason": "Agent never rebases on solo branches — commit forward or ask user to rebase manually"},
            {"args": ["cherry-pick"], "decision": "deny", "reason": "Agent never cherry-picks — open a PR with the desired commits, or ask user"},
            {"args": ["merge"],       "decision": "deny", "reason": "Agent never merges — use PRs via `gh pr create`/merge UI"},
            {"args": ["revert"],      "decision": "ask",  "reason": "Confirm revert — legitimate way to undo a bad commit"},

            # Commit gates — fail-closed
            {"fn": "git_commit_tests_fail",   "decision": "deny", "reason": "Tests/lint must pass before committing"},
            {"fn": "code_review_not_passed",  "decision": "deny", "reason": "Code review must pass before committing. Dispatch code-reviewer agent first."},
            {"fn": "verification_not_passed", "decision": "deny", "reason": "Verification must pass before committing. Dispatch api-tester and ui-tester agents first."},
            # Commit itself is allow (reversible on solo branch — see CLAUDE.md execution posture);
            # the deny gates above (tests/review/verification) are what actually protect quality.
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
    ".":       {"rules": [], "default": "deny", "reason": "`.` (source builtin) is blocked — same as `source`. If the venv isn't active, ask the user to activate it — do not try workarounds."},
    "env":     {"rules": [], "default": "deny", "reason": "env is blocked. Use module-level env reads (see .claude/rules/env-vars.md) or ask the user."},
    "xargs":   {"rules": [], "default": "deny", "reason": "xargs bypasses ACL. Use a `for` loop or run commands directly; ask the user if you need parallelism."},
    # `.venv/bin/*` is handled by the `venv_bin` check in check_command (covers ruff/pytest/etc. uniformly)
    # Standalone `python -c "…"` is denied at top level in main() — see python_c_not_after_pipe.
    "python3": {"rules": [], "default": "allow"},
    "python":  {"rules": [], "default": "allow"},
    "mongosh": {"rules": [], "default": "deny", "reason": "Use the backend and tools, not mongosh directly"},
    "gh": {
        "rules": [
            # PR read/edit operations
            {"args": ["pr", "view"],    "decision": "allow", "reason": ""},
            {"args": ["pr", "list"],    "decision": "allow", "reason": ""},
            {"args": ["pr", "checks"],  "decision": "allow", "reason": ""},
            {"args": ["pr", "diff"],    "decision": "allow", "reason": ""},
            {"args": ["pr", "status"],  "decision": "allow", "reason": ""},
            {"args": ["pr", "comment"], "decision": "deny",  "reason": "Don't post PR comments — outward-facing; ask the user to post if needed"},
            {"args": ["pr", "edit"],    "decision": "allow", "reason": ""},
            {"args": ["pr", "ready"],   "decision": "allow", "reason": ""},
            # PR create is allow (closeable/editable; solo repo, reversible — see CLAUDE.md).
            {"args": ["pr", "create"],  "decision": "allow", "reason": ""},
            # pr merge / close / delete fall through to default deny — user-only

            # Repo / runs / API / issues — read-only
            {"args": ["repo", "view"],    "decision": "allow", "reason": ""},
            {"args": ["repo", "list"],    "decision": "allow", "reason": ""},
            {"args": ["run", "view"],     "decision": "allow", "reason": ""},
            {"args": ["run", "list"],     "decision": "allow", "reason": ""},
            {"args": ["run", "watch"],    "decision": "allow", "reason": ""},
            {"args": ["api"],             "decision": "allow", "reason": ""},
            {"args": ["auth", "status"],  "decision": "allow", "reason": ""},
            {"args": ["issue", "view"],   "decision": "allow", "reason": ""},
            {"args": ["issue", "list"],   "decision": "allow", "reason": ""},
            {"args": ["issue", "comment"],"decision": "deny",  "reason": "Don't post issue comments — outward-facing; ask the user to post if needed"},
            {"args": ["issue", "create"], "decision": "ask",   "reason": "Confirm before creating issue"},
            {"args": ["workflow", "view"],"decision": "allow", "reason": ""},
            {"args": ["workflow", "list"],"decision": "allow", "reason": ""},
            {"args": ["release", "view"], "decision": "allow", "reason": ""},
            {"args": ["release", "list"], "decision": "allow", "reason": ""},
            {"args": ["secret", "list"],  "decision": "allow", "reason": ""},
        ],
        "default": "deny",
        "reason": "gh subcommand not in allow-list. Merge/close/delete/release are user-only — ask the user to run them.",
    },
    "rm": {
        "rules": [
            {"args_contain": [".env*"], "decision": "deny", "reason": "Env files blocked"},
            {"fn": "all_paths_inside_project", "decision": "allow", "reason": ""},
        ],
        "default": "deny",
        "reason": "rm only allowed inside the project tree (app/, tests/, infrastructure/, web/, tmp/, …) — system paths off-limits",
    },
    "nc":      {"rules": [], "default": "ask",  "reason": "Confirm before using nc"},
    "pip":     {"rules": [{"args": ["install"], "decision": "ask", "reason": "Confirm before installing packages"}], "default": "allow"},
    "cp":  {
        "rules": [{"args_contain": [".env*"], "decision": "deny", "reason": "Env files blocked"}],
        "default": "allow",
    },
    "mv":  {
        "rules": [
            {"args_contain": [".env*"], "decision": "deny", "reason": "Env files blocked"},
            {"args_contain": [".plan/*", ".plan"], "decision": "deny", "reason": ".plan/ is immutable staging — the orchestrator must never rename or move plan files. Branch comes from the plan's ## Metadata, not the filename."},
        ],
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
            {"fn": "curl_api_fetch_domain", "decision": "deny", "reason": "Use /api-fetch skill instead of curl for this API"},
            {"fn": "curl_no_max_time", "decision": "deny", "reason": (
                "curl requires `--max-time <seconds>` (or `-m <s>`) — without it a stalled "
                "connection blocks the call indefinitely and the harness can't kill it cleanly. "
                "Pick a bound matching the endpoint: `--max-time 5` for local/health checks, "
                "`--max-time 30` for normal API calls, `--max-time 120` for known-slow endpoints. "
                "Example: `curl -sfk --max-time 5 https://localhost:8000/health`. "
                "For a separate connect-phase cap add `--connect-timeout 2`. "
                "For waits, do NOT wrap curl in `until ... sleep ... done` (separately denied) — "
                "call an existing bounded make target (`make backend-wait`) or yield via "
                "`ScheduleWakeup` in a /loop session."
            )},
            {"fn": "curl_mutating_remote", "decision": "ask", "reason": "Confirm POST/PUT/PATCH to remote"},
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
    "stat":      {"rules": [], "default": "allow"},
    "wc":      {"rules": [], "default": "allow"},
    "true":      {"rules": [], "default": "allow"},
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
            {"args": ["push"],    "decision": "allow", "reason": ""},
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
        "default": "deny",
        "reason": "docker subcommand not in allow-list. For destructive ops (volume/network/system prune, registry login) ask the user to run them.",
    },
    "fuser":   {"rules": [], "default": "allow"},
    # `nohup` is stripped by check_command (like time/timeout) so the wrapped command gets ACL'd.
    "paste":   {"rules": [], "default": "allow"},
    "pre-commit": {"rules": [], "default": "allow"},
    "journalctl": {"rules": [], "default": "allow"},
    "du":         {"rules": [], "default": "allow"},
    "file":       {"rules": [], "default": "allow"},
    "df":         {"rules": [], "default": "allow"},
    "lsblk":      {"rules": [], "default": "allow"},
    "findmnt":    {"rules": [], "default": "allow"},
    "rmdir": {
        "rules": [{"fn": "all_paths_inside_project", "decision": "allow", "reason": ""}],
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
            {"args": ["install"],   "decision": "allow", "reason": ""},
            {"args": ["i"],         "decision": "allow", "reason": ""},
            {"args": ["ci"],        "decision": "allow", "reason": ""},
            {"args": ["uninstall"], "decision": "ask",   "reason": "Confirm npm uninstall"},
            {"args": ["update"],    "decision": "ask",   "reason": "Confirm npm update"},
            {"args": ["publish"],   "decision": "deny",  "reason": "Never publish from agent"},
            {"args": ["adduser"],   "decision": "deny",  "reason": "No npm auth changes"},
            {"args": ["login"],     "decision": "deny",  "reason": "No npm auth changes"},
            {"args": ["token"],     "decision": "deny",  "reason": "No npm tokens"},
        ],
        "default": "deny",
        "reason": "npm subcommand not in allow-list. For unusual npm flows ask the user.",
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
    "command": {"rules": [], "default": "deny", "reason": "`command <X>` is the bash `command` builtin and it bypasses ACL. Do NOT prefix anything with `command` — write the bare command. Example: instead of `command git status`, write `git status`. Instead of `command gh pr create …`, write `gh pr create …`. Retrying with the same prefix will be denied again."},
    "eval":    {"rules": [], "default": "deny", "reason": "`eval` bypasses ACL by executing a constructed string. Run the command directly."},
    "exec":    {"rules": [], "default": "deny", "reason": "`exec` bypasses ACL by replacing the shell. Run the command directly."},
    "builtin": {"rules": [], "default": "deny", "reason": "`builtin` bypasses ACL. Run the command directly."},
    "sudo":    {"rules": [], "default": "deny", "reason": "No privilege escalation from agent."},
    "pkexec":  {"rules": [], "default": "deny", "reason": "No privilege escalation from agent."},
    "doas":    {"rules": [], "default": "deny", "reason": "No privilege escalation from agent."},
    "gsutil":  {"rules": [], "default": "deny", "reason": "Use gcloud storage instead of gsutil"},
    "ffmpeg":  {"rules": [], "default": "allow", "reason": ""},
    "ffprobe": {"rules": [], "default": "allow", "reason": ""},
    "gcloud": {
        "rules": [
            # Hard denies
            {"args_contain": ["set-iam-policy", "add-iam-policy-binding", "remove-iam-policy-binding"],
             "decision": "deny", "reason": "IAM changes blocked"},
            {"args": ["auth", "activate-service-account"], "decision": "deny", "reason": "Service account impersonation blocked"},

            # Sensitive read — confirm
            {"args_contain": ["print-access-token", "print-identity-token"],
             "decision": "ask", "reason": "Confirm auth token output"},
            {"args": ["secrets", "versions", "access"],
             "decision": "ask", "reason": "Confirm secret access"},

            # Mutating verbs — confirm
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
            {"args": ["auth", "revoke"],      "decision": "deny", "reason": "Auth changes are user-only — agent shouldn't revoke credentials"},

            # Read-only — allow (broad patterns so new read subcommands don't need allow-list entries)
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
            # `gcloud builds log <id>` / `gcloud logging read` — read-only
            {"args_contain": ["log"],            "decision": "allow", "reason": ""},
            {"args_contain": ["logs"],           "decision": "allow", "reason": ""},
            {"args_contain": ["tail"],           "decision": "allow", "reason": ""},
            {"args": ["version"],                "decision": "allow", "reason": ""},
            {"args": ["help"],                   "decision": "allow", "reason": ""},
        ],
        # Default: ask, not deny. Read patterns above auto-allow; everything else (writes,
        # uncategorized commands, new subcommands) prompts for confirmation rather than
        # hard-blocking. Hard denies stay at the top of the rule list.
        "default": "ask",
        "reason": "gcloud subcommand not in read allow-list — confirm it's safe (writes/mutations should be approved).",
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
    log_dir = os.path.join(HOME, "Logs")
    os.makedirs(log_dir, exist_ok=True)
    logger = logging.getLogger("acl_hook")
    logger.setLevel(logging.INFO)
    handler = RotatingFileHandler(os.path.join(log_dir, "claude_acl.log"), maxBytes=5_000_000, backupCount=5)
    handler.namer = _gz_namer
    handler.rotator = _gz_rotator
    handler.setFormatter(logging.Formatter("[%(asctime)s] %(message)s", datefmt="%Y-%m-%d %H:%M:%S"))
    logger.addHandler(handler)
    return logger


def expand_home(arg):
    """Expand ~/path to /home/user/path."""
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
                i += 1  # skip second &
            else:
                current.append(c)
        else:
            current.append(c)
        i += 1
    parts.append("".join(current).strip())
    return [p for p in parts if p]


def arg_matches(arg, pattern):
    """Match pattern against full arg or its basename."""
    return fnmatch(arg, pattern) or fnmatch(os.path.basename(arg), pattern)


def matches_args(rule_patterns, cmd_args):
    """Ordered subsequence: each pattern matches an arg in order (not necessarily consecutive)."""
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
    """Any arg matches any pattern (unordered)."""
    for pattern in rule_patterns:
        for arg in cmd_args:
            if arg_matches(arg, pattern):
                return True
    return False


def matches_args_glob(glob_pattern, cmd_args):
    """Full argument string matched as a single glob."""
    arg_str = " ".join(cmd_args)
    return fnmatch(arg_str, glob_pattern)


LOCALHOST = ("localhost", "127.0.0.1", "0.0.0.0", "[::1]")
MUTATING_METHODS = {"POST", "PUT", "PATCH", "DELETE"}
API_FETCH_DOMAINS = ("api.vapi.ai", "api.shopmonkey.cloud", "dialpad.com", "apilayer.net", "serpapi.com", "api.scrapin.io")


def curl_api_fetch_domain(args):
    """True if curl targets a domain covered by the api-fetch skill."""
    for arg in args:
        if not arg.startswith("-"):
            for domain in API_FETCH_DOMAINS:
                if domain in arg:
                    return True
    return False


def curl_no_max_time(args):
    """True if curl actually fetches a URL but is missing `--max-time` / `-m`.

    Pure-help invocations (`curl --help`, `curl -V`) have no non-flag args and are skipped.
    Detected timeout forms: `--max-time N`, `--max-time=N`, `-m N`, `-m=N`, and short-flag
    bundles containing `m` (e.g. `-sfm 5`). curl's `-m` must end a short-flag bundle since
    it takes an argument, so any 'm' inside `-...` is the timeout flag."""
    has_positional = any(not a.startswith("-") for a in args)
    if not has_positional:
        return False
    for arg in args:
        if arg in ("--max-time", "-m"):
            return False
        if arg.startswith("--max-time=") or arg.startswith("-m="):
            return False
        if arg.startswith("-") and not arg.startswith("--") and "m" in arg[1:]:
            return False
    return True


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


def git_commit_tests_fail(args):
    """Run make test-app/test-web based on staged files. True if tests fail."""
    if "commit" not in args:
        return False

    try:
        repo = Repo(search_parent_directories=True)
    except InvalidGitRepositoryError:
        return False

    # Staged files = diff between index and HEAD
    staged = [item.a_path for item in repo.index.diff("HEAD")]
    if not staged:
        return False

    need_backend = any(f.startswith(("app/", "tests/")) for f in staged)
    need_web = any(f.startswith("web/") for f in staged)

    if not need_backend and not need_web:
        return False

    targets = []
    if need_backend:
        # `lint` runs ruff format-check + check (reads pyproject.toml — single source of truth).
        # `test-app` runs pytest + dry-run smoke checks.
        targets.extend(["lint", "test-app"])
    if need_web:
        # `test-web` already includes `npm run lint` (see Makefile).
        targets.append("test-web")

    cmd = ["make", "-j4"] + targets
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
    if result.returncode != 0:
        # Print output so the user sees what failed
        if result.stdout:
            print(result.stdout[-2000:], file=sys.stderr)
        if result.stderr:
            print(result.stderr[-2000:], file=sys.stderr)
        return True
    return False


# `python -c` is now allowed ONLY as a pipe filter (`<cmd> | python3 -c "…"`).
# Standalone `python -c "…"` is denied — it's how agents smuggle scripts past Write/ACL.
# Hard upper bound on raw Bash command size. Multi-line glue scripts above this confuse shlex
# (function defs leak as fragments), can't be reused, and bloat the transcript — split them.
MAX_BASH_LEN = 1500
MAX_BASH_LINES = 10
# sed -i with an expression longer than this is almost certainly the wrong tool —
# use Edit/Write so the change is reviewable as a diff, not a regex blob.
SED_INLINE_EXPR_MAX = 300


def rm_recursive(args):
    """True if rm is called with recursive flags (-r, -R, --recursive, or combos like -rf)."""
    for arg in args:
        if arg == "--recursive":
            return True
        if arg.startswith("-") and not arg.startswith("--") and ("r" in arg or "R" in arg):
            return True
    return False


def all_paths_inside_project(args):
    """True iff every non-flag path arg resolves inside PROJECT_DIR (and at least one exists).

    Used by `rm` and `rmdir` as an allow-gate paired with default=deny — deletions are off
    by default and turn on only when every target is inside the codebase (app/, tests/,
    infrastructure/, web/, tmp/, …). No path args = no allow → falls to default deny.
    """
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
    """True if `sed -i` is called with a single expression longer than SED_INLINE_EXPR_MAX.

    Long inline sed scripts hide the change behind a regex blob — use Edit/Write so the
    diff is reviewable. `-e` chains and short `s/X/Y/` substitutions are still allowed.
    """
    if "-i" not in args and not any(a.startswith("-i") for a in args):
        return False
    for arg in args:
        if arg.startswith("-"):
            continue
        # Heuristic: first positional that looks like a sed expression
        if any(tok in arg for tok in ("s|", "s/", "s#", "s@")):
            return len(arg) > SED_INLINE_EXPR_MAX
    return False


def verification_not_passed(args, *, session_id: str):
    """True if .work/{project}/verification.md is missing or lacks VERDICT: PASSED."""
    if "commit" not in args:
        return False

    # Skip verification for docs-only changes (no app/web/tests files staged)
    repo = Repo(search_parent_directories=True)
    staged = [item.a_path for item in repo.index.diff("HEAD") if item.a_path]
    if staged and not staged_has_code_changes(staged):
        return False

    work_dir = find_work_dir(session_id=session_id)
    if work_dir is None:
        return False  # Non-workflow branch, skip

    verification_file = get_artifact_path(session_id=session_id, artifact="verification")
    if verification_file is None:
        return False
    if not verification_file.exists():
        return True  # Missing = not passed

    first_line = verification_file.read_text().split("\n", 1)[0].strip()
    return not first_line.startswith("VERDICT: PASSED")


def code_review_not_passed(args, *, session_id: str):
    """True if session code_review artifact is missing or lacks VERDICT: PASSED."""
    if "commit" not in args:
        return False

    repo = Repo(search_parent_directories=True)
    staged = [item.a_path for item in repo.index.diff("HEAD") if item.a_path]
    if staged and not staged_has_code_changes(staged):
        return False

    work_dir = find_work_dir(session_id=session_id)
    if work_dir is None:
        return False

    code_review_file = get_artifact_path(session_id=session_id, artifact="code_review")
    if code_review_file is None:
        return False
    if not code_review_file.exists():
        return True

    first_line = code_review_file.read_text().split("\n", 1)[0].strip()
    return not first_line.startswith("VERDICT: PASSED")


CUSTOM_FNS = {
    "curl_api_fetch_domain": curl_api_fetch_domain,
    "curl_no_max_time": curl_no_max_time,
    "curl_mutating_remote": curl_mutating_remote,
    "sed_inline_long": sed_inline_long,
    "rm_recursive": rm_recursive,
    "all_paths_inside_project": all_paths_inside_project,
    "git_commit_tests_fail": git_commit_tests_fail,
    "code_review_not_passed": code_review_not_passed,
    "verification_not_passed": verification_not_passed,
}

SESSION_AWARE_FNS = {"code_review_not_passed", "verification_not_passed"}


def check_rule(rule, cmd_args, *, session_id: str):
    """Check if a single rule matches the command args."""
    if "fn" in rule:
        fn_name = rule["fn"]
        if fn_name in SESSION_AWARE_FNS:
            return CUSTOM_FNS[fn_name](cmd_args, session_id=session_id)
        return CUSTOM_FNS[fn_name](cmd_args)
    if "args" in rule:
        return matches_args(rule["args"], cmd_args)
    if "args_contain" in rule:
        return matches_args_contain(rule["args_contain"], cmd_args)
    if "args_glob" in rule:
        return matches_args_glob(rule["args_glob"], cmd_args)
    return False


SAFE_HEREDOC_RE = re.compile(
    r"""\A\s*
        cat\s+                                   # literal cat command
        >>\s*                                    # append redirect (not overwrite)
        (?P<path>[^\s<]+)                        # target path token
        \s+
        <<-?\s*                                  # heredoc start, optional dash
        (?P<quote>['"])(?P<delim>[A-Za-z_]\w*)(?P=quote)   # REQUIRED quoted delimiter
        \s*\n
        .*?                                      # body (DOTALL, non-greedy)
        \n(?P=delim)\s*                          # closing delimiter at line start
        \Z                                       # strict end-of-string — no trailing payload
    """,
    re.VERBOSE | re.DOTALL,
)


def is_safe_heredoc_append(command: str) -> bool:
    """True if command is `cat >> <path> << 'DELIM' ... DELIM` with path under .work/ or .plan/.

    Quoted delimiter is required — it disables shell expansion inside the body,
    preventing $(cmd) / $var injection. Unquoted heredocs fall through to the
    blanket deny."""
    m = SAFE_HEREDOC_RE.match(command)
    if not m:
        return False
    path = m.group("path")
    real = os.path.realpath(path if os.path.isabs(path) else os.path.join(PROJECT_DIR, path))
    safe_roots = (
        os.path.join(PROJECT_DIR, ".work") + "/",
        os.path.join(PROJECT_DIR, ".plan") + "/",
    )
    return real.startswith(safe_roots)


# --- Top-level antipattern detectors (run BEFORE split_chained_commands in main()) ---
#
# These walk the bashlex AST, not the raw string. Regex on raw bash command text
# can't tell `python -c` inside a quoted echo string from a real `python -c`
# invocation, and can't distinguish `for ...; do sleep N; check; done` (bad —
# chained sleep) from `until X; do sleep 2; done` (legit — condition polling).
# The AST gives parent-context for free: pipeline vs list vs compound, sibling
# vs lone child.


def _walk_with_parent(node, parent=None, position=None):
    """Yield (node, parent, position_in_parent) for every node in the AST."""
    yield node, parent, position
    children = list(_node_children(node))
    for idx, child in enumerate(children):
        yield from _walk_with_parent(child, parent=node, position=idx)


def _node_children(node):
    """Yield the structural children of a bashlex node, in document order.

    Handles the three child-attribute shapes bashlex uses: `.parts` (most nodes),
    `.list` (CompoundNode), `.command` (CommandsubstitutionNode / ProcesssubstitutionNode).
    """
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
    """For a CommandNode, return its [argv0, argv1, …] word list. Other nodes → []."""
    if getattr(node, "kind", None) != "command":
        return []
    words = []
    for part in getattr(node, "parts", []) or []:
        if getattr(part, "kind", None) == "word":
            words.append(part.word)
    return words


# These three detectors take ALREADY-PARSED bashlex trees. Parsing happens
# exactly once in main(), which denies on bashlex exception so a parse-defeating
# command never silently slips past these checks. Tests construct trees via
# `bashlex.parse(cmd)` and pass them in — see tests/hooks/conftest.py.


def has_function_def(trees) -> bool:
    """True iff any tree contains a Bash function definition.

    Catches both POSIX (`name() { … }`) and bash-keyword (`function name { … }`,
    `function name() { … }`) forms — bashlex normalises all of them to FunctionNode.
    Quoted literals inside echo strings do NOT trigger because the AST keeps them
    as WordNode payloads, not FunctionNode.
    """
    for tree in trees:
        for node, _parent, _pos in _walk_with_parent(tree):
            if getattr(node, "kind", None) == "function":
                return True
    return False


def python_c_not_after_pipe(trees) -> bool:
    """True iff any `python[3] -c …` invocation is NOT positioned as a pipe receiver.

    Allowed:  `<src> | python3 -c "…"`         (legit stdin filter — only allowed shape)
    Denied:   `python3 -c "…"`                 (standalone — should be a script file)
              `cmd && python3 -c "…"`          (chained via &&/||/;, parent is ListNode)
              `var=$(python3 -c "…")`          (command substitution — same antipattern)
              `false || python3 -c "…"`        (logical OR, not a pipe)
    """
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
            # Only acceptable shape: this CommandNode is a non-first part of a PipelineNode.
            if parent is not None and getattr(parent, "kind", None) == "pipeline" and (position or 0) > 0:
                continue
            return True
    return False


def until_loop_with_sleep(trees) -> bool:
    """True iff a Bash invocation contains both `until` (reserved word) and a `sleep` command.

    Catches the inline polling pattern `until <cond>; do … sleep N … ; done`. While the
    loop is running the agent yields nothing to the harness, can't be cleanly interrupted,
    and burns wakeup budget watching a single Bash call block. The pattern is replaced by:
      (1) call an existing bounded wait target (`make backend-wait` already encapsulates
          curl polling with a retry cap), or
      (2) `ScheduleWakeup(delaySeconds=…)` inside a /loop session so the agent yields
          between iterations and the prompt cache stays warm under 270s, or
      (3) `/schedule` (CronCreate) for fire-and-forget recurring runs.

    AST-based: a top-level word `until` parses as a ReservedwordNode, while a quoted
    `echo "until ..."` keeps it as a WordNode payload — so this does not falsely fire
    on string literals. A `sleep` inside the same bash invocation (any nesting) is the
    second necessary signal. Together they indicate the polling shape; alone, each is
    fine and remains allowed.

    Stricter than `chained_sleep` — that detector allowed `until <X>; do sleep N; done`
    when sleep had no sibling commands. With agent-yielding wait tools available, the
    until shape itself is the antipattern, not just the sibling-chained variant.
    """
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
    """True iff `sleep N` is chained with another command at the same nesting level.

    Denied:   `sleep 90 && python foo.py`              (ListNode with sibling commands)
              `sleep 5; echo done`                     (ditto, separator is `;`)
              `for i in 1 2 3; do sleep 30; check; done`  (do-body has sibling commands)
    Allowed:  `sleep 5`                                (lone CommandNode, no siblings)
              `while true; do sleep 2; done`           (sleep is the only do-body command;
                                                        until+sleep is banned separately by
                                                        until_loop_with_sleep)
    """
    for tree in trees:
        for node, parent, _pos in _walk_with_parent(tree):
            words = _command_words(node)
            if not words or os.path.basename(words[0]) != "sleep":
                continue
            if parent is None:
                continue  # standalone top-level `sleep N` → allow
            siblings = [c for c in _node_children(parent) if c is not node and getattr(c, "kind", None) == "command"]
            if siblings:
                return True
    return False


def check_command(cmd_str, logger, *, session_id: str, agent_type: str):
    """Check a single command against ACL. Returns (decision, reason, log_detail)."""
    try:
        parts = shlex.split(cmd_str)
    except ValueError as e:
        reason = (
            f"Bash command failed to parse ({e}). Don't reach for clever escapes/quoting — "
            "rewrite as a simpler primitive the shell can parse cleanly, or split into "
            "multiple Bash calls."
        )
        logger.info('decision=deny command="%s" matched=shlex_error agent=%s', cmd_str[:120], agent_type)
        return "deny", reason, "shlex_error"

    if not parts:
        return "allow", "", "empty command"

    # Skip leading VAR=value environment variable assignments to find real command
    while parts and "=" in parts[0] and not parts[0].startswith("-"):
        parts = parts[1:]

    # Strip leading process-wrapper commands so the underlying command gets ACL'd, not the wrapper.
    # `nohup` is the dangerous case — without stripping, `nohup git reset --hard` only ACLs `nohup`
    # which defaults to allow. Other wrappers (nice/setsid/stdbuf/ionice/taskset) similarly pass
    # the real command through unchanged.
    while parts and parts[0] in ("time", "nohup", "nice", "setsid", "stdbuf", "ionice", "taskset"):
        parts = parts[1:]
        # `nice -n 5` / `ionice -c 3` / `taskset -c 0`: skip wrapper flags
        while parts and parts[0].startswith("-"):
            if parts[0] in ("-n", "-c", "-p") and len(parts) > 1:
                parts = parts[2:]
            else:
                parts = parts[1:]

    # Strip leading `timeout [opts] <duration>` wrapper. Handles `timeout 5 cmd`,
    # `timeout -k 1 5 cmd`, `timeout --signal=KILL 5 cmd`.
    if parts and parts[0] == "timeout":
        parts = parts[1:]
        while parts and parts[0].startswith("-"):
            if parts[0] in ("-s", "--signal", "-k", "--kill-after") and len(parts) > 1:
                parts = parts[2:]
            else:
                parts = parts[1:]
        if parts:  # drop the duration arg
            parts = parts[1:]

    if not parts:
        return "allow", "", "empty command"

    command = parts[0]

    if command.startswith("#"):
        return "allow", "", "comment"
    args = [expand_home(a) for a in parts[1:]]

    if fnmatch(command, ".claude/skills/*/*.py") or fnmatch(command, "*/.claude/skills/*/*.py") or fnmatch(command, "*/.claude/hooks/*.py"):
        logger.info('decision=allow command="%s" matched=claude_script agent=%s', cmd_str, agent_type)
        return "allow", "", "claude_script"

    # Deny any direct path into the project venv — bare command name should be used instead
    # (venv is activated by the shell profile). MUST run before basename normalization, which
    # would strip `.venv/bin/` and short-circuit this check.
    if "/" in command:
        # Use abspath (not realpath) so symlinks like `.venv/bin/python3 -> /usr/bin/python3` still
        # match the venv_bin rule on the path the agent typed, not the symlink target.
        abs_command = os.path.abspath(command if os.path.isabs(command) else os.path.join(PROJECT_DIR, command))
        venv_bin_prefix = os.path.join(PROJECT_DIR, ".venv", "bin") + os.sep
        if abs_command.startswith(venv_bin_prefix):
            bare = os.path.basename(abs_command)
            reason = (
                f"Don't invoke `{command}` — call `{bare}` directly. The project venv should be active in the shell profile.\n"
                f"If `{bare}` still fails (ModuleNotFoundError, wrong interpreter), the venv isn't active in this session — "
                f"ASK THE USER to activate it (`source .venv/bin/activate` in their terminal). "
                f"Do NOT try workarounds: `source`, `.`, `bash -c`, `env VIRTUAL_ENV=…`, or invoking the venv binary by path are all blocked."
            )
            logger.info('decision=deny command="%s" matched=venv_bin agent=%s', cmd_str, agent_type)
            return "deny", reason, "venv_bin"

    # Same idea for system-wide python paths (`/usr/bin/python3 -m pytest`) — keep using bare `python3`.
    basename = os.path.basename(command)
    if "/" in command and basename in ("python", "python3"):
        reason = (
            "Use python3 directly, not a path. The project venv should be active in the shell profile.\n"
            "If `python3` runs from /usr/bin (venv not active in this session), ASK THE USER to activate it — "
            "do not try `source`, `.`, `bash -c`, or other workarounds (all blocked)."
        )
        logger.info('decision=deny command="%s" matched=python_path agent=%s', cmd_str, agent_type)
        return "deny", reason, "python_path"

    # Basename normalization: `/usr/bin/git`, `/usr/local/bin/gcloud` etc. must be ACL'd same
    # as the bare `git` / `gcloud`. Without this, path-prefixed forms fall through to
    # unknown_command → ask, which is weaker than the bare command's deny rules.
    if "/" in command:
        bn = os.path.basename(command)
        if bn in ACL:
            command = bn

    if command not in ACL:
        reason = (
            f"Unknown command `{command}` — not in ACL. Don't smuggle it through a wrapper "
            "or a clever one-liner. Use a simpler primitive that IS in the allow-list "
            "(ls/cat/grep/find/git/gh/…), or split into multiple Bash calls so each step "
            "is checkable. If you genuinely need this command, ask the user to add it to ACL."
        )
        logger.info('decision=deny command="%s" matched=unknown_command agent=%s', cmd_str, agent_type)
        return "deny", reason, "unknown_command"

    entry = ACL[command]
    for rule in entry["rules"]:
        if check_rule(rule, args, session_id=session_id):
            logger.info(
                'decision=%s command="%s" matched=rule:%s agent=%s',
                rule["decision"],
                cmd_str,
                rule.get("args") or rule.get("args_contain") or rule.get("args_glob"),
                agent_type,
            )
            return rule["decision"], rule["reason"], f"rule"

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

    # Safe heredoc: `cat >> <.work/.plan path> << 'DELIM'` is allowed — that's how
    # subagents append progress entries. Must short-circuit BEFORE split_chained_commands
    # runs (which cannot parse heredoc bodies and would split on | / ; in the body).
    if is_safe_heredoc_append(command):
        logger.info('decision=allow command="%s" matched=safe_heredoc agent=%s', command[:120], agent_type)
        print(json.dumps({"hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "allow",
        }}))
        return

    # Long multi-line bash blobs are the wrong tool — split into multiple simple calls
    # so each step gets its own result, ACL check, and feedback. File-as-script is the
    # fallback for genuinely atomic scripts (rare); don't use it as a workaround.
    line_count = command.count("\n") + 1
    if len(command) > MAX_BASH_LEN or line_count > MAX_BASH_LINES:
        logger.info('decision=deny command_too_long len=%d lines=%d agent=%s', len(command), line_count, agent_type)
        print(json.dumps({"hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "deny",
            "permissionDecisionReason": (
                f"Bash command too large ({len(command)} chars / {line_count} lines; "
                f"limit {MAX_BASH_LEN}/{MAX_BASH_LINES}). SPLIT into several simple Bash "
                "calls — each step gets its own ACL check and feedback. Antipatterns to "
                "avoid: `for x in …; do …; done`, function defs `name() {…}`, `&&` chains "
                "longer than 3 links, `python -c \"<multiline script>\"`. Retrying with "
                "cosmetic shortening (10–20 chars off) will hit this same limit. "
                "Genuinely atomic script with control flow (rare) → Write tool to a file, then run it."
            ),
        }}))
        return

    # Agents must use Write tool for file creation — heredoc content breaks ACL parsing
    if "<<" in command:
        logger.info('decision=deny command="%s" matched=agent_heredoc agent=%s', command[:120], agent_type)
        print(json.dumps({"hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "deny",
            "permissionDecisionReason": "Agents cannot use heredoc (<<) in Bash — use the Write tool instead.",
        }}))
        return

    # Parse bash once; deny if bashlex chokes. Fail-closed: the three AST detectors
    # below cannot inspect what they can't parse, so an unparseable command is
    # never silently allowed. Common triggers: ANSI-C `$'…'`, process substitution
    # `<(…)`/`>(…)`, unbalanced quotes — rare in legit usage.
    try:
        trees = bashlex.parse(command)
    except Exception as e:
        logger.info('decision=deny command="%s" matched=bashlex_parse_failed agent=%s err=%s', command[:120], agent_type, type(e).__name__)
        print(json.dumps({"hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "deny",
            "permissionDecisionReason": (
                f"Bash command failed to parse via bashlex ({type(e).__name__}): {e}. "
                "This blocks the AST-based antipattern detectors (python -c, function "
                "defs, chained sleep) from checking it, so we fail closed. Likely cause: "
                "ANSI-C escapes (`$'…'`), process substitution (`<(…)` / `>(…)`), "
                "unbalanced quotes, or another esoteric construct. Rewrite as a simpler "
                "primitive (plain quotes, `$(…)` instead of `<(…)`) or split into "
                "multiple Bash calls."
            ),
        }}))
        return

    # Bash function definitions inside a one-shot command are always wrong shape —
    # they leak as `unknown_command` fragments via shlex misparse, and the work
    # should be expressed as separate calls.
    if has_function_def(trees):
        logger.info('decision=deny command="%s" matched=function_def agent=%s', command[:120], agent_type)
        print(json.dumps({"hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "deny",
            "permissionDecisionReason": (
                "Bash function definitions (`name() { … }`) inside a Bash call are denied — "
                "split the work into multiple simple Bash calls. If you need reusable logic, "
                "Write it as a script file. Function defs in one-shot commands shlex-misparse "
                "and leak fragments past ACL."
            ),
        }}))
        return

    # `python -c "..."` standalone is how agents smuggle scripts past Write/ACL.
    # Allowed ONLY as a pipe filter (`<cmd> | python3 -c "…"`).
    if python_c_not_after_pipe(trees):
        logger.info('decision=deny command="%s" matched=python_c_standalone agent=%s', command[:120], agent_type)
        print(json.dumps({"hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "deny",
            "permissionDecisionReason": (
                "`python -c` is allowed only as a pipe filter (`<cmd> | python3 -c \"import "
                "json,sys; …\"`). Standalone or `$(python -c …)` is a script masquerading as "
                "a command. Options: (1) pipe data in — `gcloud … | python3 -c \"…\"`; "
                "(2) if it's a real script — Write tool → `scripts/<name>.py` → run "
                "`python3 scripts/<name>.py`; (3) one-off computation — split into simple "
                "Bash builtins or `jq`."
            ),
        }}))
        return

    # `until <cond>; do …sleep…; done` polling — the loop blocks the harness across all
    # iterations, can't be cleanly interrupted, and burns wakeup budget watching a single
    # Bash call. Force agent-yielding waits instead.
    if until_loop_with_sleep(trees):
        logger.info('decision=deny command="%s" matched=until_loop_with_sleep agent=%s', command[:120], agent_type)
        print(json.dumps({"hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "deny",
            "permissionDecisionReason": (
                "`until <cond>; do … sleep N … ; done` inline polling is denied — see "
                ".claude/rules/waiting.md. The loop blocks the harness for the whole wait, "
                "can't be interrupted cleanly, and the agent yields nothing between iterations. "
                "Use instead: "
                "(1) an existing bounded wait target — `make backend-wait` already wraps curl "
                "polling with a retry cap; if no target exists, add one; "
                "(2) `ScheduleWakeup(delaySeconds=…, prompt=<same /loop input>)` inside a /loop "
                "session — the agent yields between checks and the prompt cache stays warm "
                "under 270s; "
                "(3) `/schedule` (CronCreate) for recurring or fire-and-forget runs; "
                "(4) for a foreground command you started, `Bash(..., run_in_background=true)` "
                "plus `Monitor`/`BashOutput` instead of polling its result with curl."
            ),
        }}))
        return

    # `sleep N (&&|;|\\|) <cmd>` — the harness blocks this too, but we deny first with
    # a project-specific message pointing at the right wait tools.
    if chained_sleep(trees):
        logger.info('decision=deny command="%s" matched=chained_sleep agent=%s', command[:120], agent_type)
        print(json.dumps({"hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "deny",
            "permissionDecisionReason": (
                "`sleep N` chained with another command is denied — see .claude/rules/waiting.md. "
                "Use one of: (1) `Bash(..., run_in_background=true)` + `Monitor`/`BashOutput` for "
                "a command you started; (2) an existing bounded wait target like `make backend-wait` "
                "(curl polling encapsulated with a retry cap); (3) `ScheduleWakeup` (in a /loop "
                "session) to come back later with cached context; (4) `/schedule` for a cron remote "
                "agent. Do NOT chain `sleep N && <cmd>` — the harness blocks it and shortening "
                "sleeps doesn't help. Do NOT wrap the wait in `until ... sleep ... done` either — "
                "that shape is separately denied (see until_loop_with_sleep)."
            ),
        }}))
        return

    sub_commands = split_chained_commands(command)

    final_decision = "allow"
    final_reason = ""

    session_id = data["session_id"]
    for sub_cmd in sub_commands:
        decision, reason, _ = check_command(sub_cmd, logger, session_id=session_id, agent_type=agent_type)
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
