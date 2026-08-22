#!/usr/bin/env python3
"""ACL hook for Claude Code Bash commands.

Single job: decide allow / ask / deny for each Bash invocation, so the user
only sees prompts for genuinely ambiguous commands. No project knowledge, no
harness gates, no verification / review checks.

Rule match types:
  "args"         — ordered subsequence match (each pattern matches an arg in order)
  "args_contain" — any arg matches any pattern (unordered)
  "args_glob"    — full argument string matched as a single glob
"""

from __future__ import annotations

import gzip
import json
import logging
import os
import shlex
import subprocess
import sys
import tempfile
from fnmatch import fnmatch
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import TYPE_CHECKING, NamedTuple, TypedDict

import bashlex

if TYPE_CHECKING:
    from collections.abc import Callable, Iterable, Iterator

# Bashlex AST nodes are duck-typed; we read attributes via getattr.
BashNode = object


class _RuleBase(TypedDict):
    decision: str


class Rule(_RuleBase, total=False):
    """One ACL rule. Exactly one of `args` / `args_contain` / `args_glob` / `fn` is set."""

    args: list[str]
    args_contain: list[str]
    args_glob: str
    fn: str
    reason: str


class _EntryBase(TypedDict):
    default: str


class Entry(_EntryBase, total=False):
    """ACL entry for a single command name. `rules` are tried in order; `default` is the fallback."""

    rules: list[Rule]
    reason: str


HOME = str(Path.home())
# Project root passed by Claude Code as CLAUDE_PROJECT_DIR. Fall back to cwd
# when invoked outside a Claude Code session (tests, manual runs).
PROJECT_DIR = str(Path(os.environ.get("CLAUDE_PROJECT_DIR") or Path.cwd()).resolve())

# The Bash call's own cwd, filled in main() from the payload — HEAD is per-worktree, PROJECT_DIR is
# not (see `_head_file`). Shared refs (refs/heads, refs/remotes) live in the main `.git` either way.
_INVOCATION: dict[str, str] = {"cwd": PROJECT_DIR}

# ── ACL config: the bundled table, read in place ─────────────────────────────
#
# The rule table lives in `acl.json` next to this file and is read straight from the
# plugin — there is no per-project copy to install, sync, or hand-edit, so every
# project always runs the rules that ship with the installed plugin version. Change
# rules by editing `acl.json` and bumping the plugin `version`.

_ACL_PATH = Path(__file__).parent / "acl.json"
_ACL_CACHE: dict[str, Entry] | None = None


def _load_acl() -> dict[str, Entry]:
    """Parse the bundled ACL table (cached — one hook run checks many sub-commands)."""
    global _ACL_CACHE  # noqa: PLW0603 — module-level cache for the parsed config
    if _ACL_CACHE is not None:
        return _ACL_CACHE
    loaded: dict[str, Entry] = json.loads(_ACL_PATH.read_text(encoding="utf-8"))
    _ACL_CACHE = loaded
    return loaded


def acl() -> dict[str, Entry]:
    """Public accessor for the loaded ACL table (tests reset _ACL_CACHE to reload)."""
    return _load_acl()


DECISION_PRIORITY = {"deny": 2, "ask": 1, "allow": 0}

# ── Logging ──────────────────────────────────────────────────────────────────


def _gz_namer(name: str) -> str:
    return name + ".gz"


def _gz_rotator(source: str, dest: str) -> None:
    src = Path(source)
    with src.open("rb") as f_in, gzip.open(dest, "wb") as f_out:
        f_out.write(f_in.read())
    src.unlink()


# Where to look when you wonder "why was that denied?" — one line per sub-command decision plus a
# `final=` line per Bash call. Rotated at 5 MB, 5 gzipped generations (`acl-hook.log.1.gz`, …).
LOG_PATH = Path(HOME) / ".claude" / "logs" / "acl-hook.log"


def for_log(command: str) -> str:
    """One-line, untruncated rendering of a command — the log must show exactly what was judged."""
    return command.replace("\n", "\\n")


def setup_logging() -> logging.Logger:
    """Initialise the rotating file logger used by every ACL decision."""
    logger = logging.getLogger("acl_hook")
    if logger.handlers:  # one process may decide several commands (tests); don't stack handlers
        return logger
    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    logger.setLevel(logging.INFO)
    handler = RotatingFileHandler(LOG_PATH, maxBytes=5_000_000, backupCount=5)
    handler.namer = _gz_namer
    handler.rotator = _gz_rotator
    handler.setFormatter(logging.Formatter("[%(asctime)s] %(message)s", datefmt="%Y-%m-%d %H:%M:%S"))
    logger.addHandler(handler)
    return logger


# ── Arg utilities ────────────────────────────────────────────────────────────


def expand_home(arg: str) -> str:
    """Expand a leading `~` or `~/` to $HOME (other forms left alone)."""
    if arg == "~":
        return HOME
    if arg.startswith("~/"):
        return HOME + arg[1:]
    return arg


class Span(NamedTuple):
    """Half-open range into the original command string."""

    start: int
    end: int


def _separator_spans(command: str) -> Iterator[Span]:
    r"""Yield every top-level `&&` / `;` / `|` / newline outside quotes as a Span.

    A newline separates commands exactly like `;` does. Without it, `ls\ngit push --force` parsed as
    one `ls` invocation with the rest as arguments — every line after the first went unchecked.
    """
    in_single = in_double = False
    i = 0
    while i < len(command):
        c = command[i]
        if c == "'" and not in_double:
            in_single = not in_single
        elif c == '"' and not in_single:
            in_double = not in_double
        elif not (in_single or in_double):
            if c in {"|", ";", "\n"}:
                yield Span(i, i + 1)
            elif c == "&" and command[i + 1 : i + 2] == "&":
                yield Span(i, i + 2)
                i += 1
        i += 1


def split_chained_commands(command: str) -> list[str]:
    """Split a Bash command on top-level `&&`, `;`, `|`, newline — respecting quotes."""
    pieces: list[str] = []
    cursor = 0
    for start, end in _separator_spans(command):
        pieces.append(command[cursor:start])
        cursor = end
    pieces.append(command[cursor:])
    return [p.strip() for p in pieces if p.strip()]


def arg_matches(arg: str, pattern: str) -> bool:
    """Glob-match `arg` against `pattern`, also trying just the basename."""
    return fnmatch(arg, pattern) or fnmatch(Path(arg).name, pattern)


def matches_args(rule_patterns: list[str], cmd_args: list[str]) -> bool:
    """True iff `rule_patterns` appear as an ordered subsequence of `cmd_args`."""
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


def matches_args_contain(rule_patterns: list[str], cmd_args: list[str]) -> bool:
    """True iff any pattern matches any arg (unordered membership test)."""
    return any(arg_matches(arg, pattern) for pattern in rule_patterns for arg in cmd_args)


def matches_args_glob(glob_pattern: str, cmd_args: list[str]) -> bool:
    """Match the full arg string (space-joined) as a single glob."""
    return fnmatch(" ".join(cmd_args), glob_pattern)


# ── Custom predicates referenced via {"fn": "..."} in ACL rules ─────────────

LOCALHOST = ("localhost", "127.0.0.1", "0.0.0.0", "[::1]")  # noqa: S104 — identifier list, not a network bind
MUTATING_METHODS = {"POST", "PUT", "PATCH", "DELETE"}

_X_FLAG_ARG_SPAN = 2  # `-X METHOD` consumes two argv entries


def curl_mutating_remote(args: list[str]) -> bool:
    """True if curl uses a mutating method against a non-localhost target."""
    mutating = False
    for i, arg in enumerate(args):
        if arg in ("-X", "--request") and i + 1 < len(args) and args[i + 1].upper() in MUTATING_METHODS:
            mutating = True
        if arg.startswith("-X") and len(arg) > _X_FLAG_ARG_SPAN and arg[_X_FLAG_ARG_SPAN:].upper() in MUTATING_METHODS:
            mutating = True
        if arg in ("-d", "--data", "--data-raw", "--data-binary", "--data-urlencode"):
            mutating = True
    if not mutating:
        return False
    return not any(not arg.startswith("-") and any(h in arg for h in LOCALHOST) for arg in args)


# Standalone `python -c "…"` is gated in _ast_gate — see python_c_not_after_pipe.
MAX_BASH_LEN = 1500
MAX_BASH_LINES = 10
SED_INLINE_EXPR_MAX = 300
# A standalone `python3 -c` is allowed up to this length on a single line (the import/version
# introspection one-liners the agent needs); longer/multiline scripts go to a file (reviewability).
PYTHON_C_INLINE_MAX = 200
# Cap every detached command, whatever its shape: `run_in_background: true` is the one case the
# harness does not bound at all (a foreground call dies at its tool timeout, a detached one runs
# until it exits on its own). Shape-blind on purpose — see CLAUDE.md, "Hanging". Generous, because
# backgrounding is what you do for slow work.
BACKGROUND_TIMEOUT_SECONDS = 1800


def rm_recursive(args: list[str]) -> bool:
    """True iff `rm` was invoked with a recursive flag."""
    for arg in args:
        if arg == "--recursive":
            return True
        if arg.startswith("-") and not arg.startswith("--") and ("r" in arg or "R" in arg):
            return True
    return False


def all_paths_inside_project(args: list[str]) -> bool:
    """True iff every non-flag path arg resolves inside PROJECT_DIR (and at least one exists)."""
    project_root = Path(PROJECT_DIR).resolve()
    has_path = False
    for arg in args:
        if arg.startswith("-"):
            continue
        has_path = True
        candidate = Path(arg) if Path(arg).is_absolute() else project_root / arg
        real = candidate.resolve()
        if real != project_root and project_root not in real.parents:
            return False
    return has_path


# The agent's scratch dir: the ONE place `rm` is allowed. A root-level hidden dir (NOT under
# `.claude/`, whose edits the harness prompts for — that's why scratch can't live there) that
# won't collide with a project's own top-level `tmp/`.
SCRATCH_SUBDIR = ".scratch"


def all_paths_under_scratch(args: list[str]) -> bool:
    """True iff every non-flag path arg resolves inside the scratch dir (`.scratch/`).

    Existence is NOT required — `rm -f .claude/tmp/maybe-gone` is fine. `resolve()` collapses any
    `..` traversal, so `rm .claude/tmp/../../etc/x` lands outside scratch and returns False (deny).
    """
    project_root = Path(PROJECT_DIR).resolve()
    scratch_root = (project_root / SCRATCH_SUBDIR).resolve()
    has_path = False
    for arg in args:
        if arg.startswith("-"):
            continue
        has_path = True
        candidate = Path(arg) if Path(arg).is_absolute() else project_root / arg
        real = candidate.resolve()
        if real != scratch_root and scratch_root not in real.parents:
            return False
    return has_path


# Where a write may land outside the project. Everything else out there is the user's machine:
# `~/.bashrc`, `~/.ssh/authorized_keys`, `/etc/hosts` — files no task needs to overwrite, and
# whose damage no `git checkout` undoes. `/dev/*` are sinks, and both temp roots are areas the
# harness already hands the agent for throwaway output.
_WRITABLE_OUTSIDE = ("/dev/", tempfile.gettempdir() + "/", str(Path.home() / ".claude" / "jobs") + "/")


def write_escapes_project(target: str) -> bool:
    """True iff writing to `target` lands outside the project, or inside its `.git`.

    One boundary, shared by every way a Bash call writes a file — a `>` redirect, or a program
    like `tee` that takes the path as an argument.
    """
    if target.startswith(_WRITABLE_OUTSIDE):
        return False
    project_root = Path(PROJECT_DIR).resolve()
    # `expanduser` first: the shell expands `~` before the write happens, and treating
    # `~/.bashrc` as a relative path would place the user's dotfile inside the project.
    expanded = Path(target).expanduser()
    candidate = expanded if expanded.is_absolute() else project_root / expanded
    real = candidate.resolve()
    if project_root not in real.parents:
        return True
    return any(part == ".git" for part in real.relative_to(project_root).parts)


def tee_writes_outside_project(args: list[str]) -> bool:
    """True iff `tee` was given a path outside the project — the redirect hole, spelled as a program."""
    return any(write_escapes_project(arg) for arg in args if not arg.startswith("-"))


def ensure_scratch_dir() -> None:
    """Guarantee `<project>/.scratch/` exists and is gitignored — the one dir where `rm` is allowed.

    The hook owns the scratch area it polices, so the agent never has to `mkdir` it or hand-edit
    `.gitignore` (and never gets prompted for either). Idempotent and cheap: `mkdir(exist_ok)` plus
    a one-line append done only when the entry is absent — so it's safe on every invocation and
    recreates the dir if a prior `rm -rf .scratch` removed it.
    """
    project_root = Path(PROJECT_DIR)
    (project_root / SCRATCH_SUBDIR).mkdir(parents=True, exist_ok=True)
    gitignore = project_root / ".gitignore"
    entry = f"{SCRATCH_SUBDIR}/"
    existing = gitignore.read_text(encoding="utf-8") if gitignore.exists() else ""
    if entry in existing.splitlines():
        return
    prefix = "" if existing == "" or existing.endswith("\n") else "\n"
    with gitignore.open("a", encoding="utf-8") as fh:
        fh.write(f"{prefix}{entry}\n")


def sed_inline_long(args: list[str]) -> bool:
    """True iff `sed -i` is passed a single substitution expression longer than the limit."""
    if "-i" not in args and not any(a.startswith("-i") for a in args):
        return False
    for arg in args:
        if arg.startswith("-"):
            continue
        if any(tok in arg for tok in ("s|", "s/", "s#", "s@")):
            return len(arg) > SED_INLINE_EXPR_MAX
    return False


_GIT_CONFIG_WRITE_FLAGS = {
    "--add",
    "--unset",
    "--unset-all",
    "--replace-all",
    "--rename-section",
    "--remove-section",
    "--edit",
    "-e",
}


def git_config_read(args: list[str]) -> bool:
    """True iff a `git config …` invocation only reads (sets no value, uses no mutating flag).

    A read sets at most one positional — the key, e.g. `git config user.name` — and no write flag.
    A write either sets a value (`git config user.name X`: two positionals) or carries --add/--unset/
    etc. Scope flags (`--global`/`--local`) and read flags (`--get`/`--list`) start with `-`, so they
    don't count as positionals. This is the read/write distinction the args matchers can't make:
    `git config user.name` (read) and `git config user.name X` (write) share the same `config user.name`
    prefix, so an ordered-subsequence rule can't tell them apart.
    """
    if not args or args[0] != "config":
        return False
    rest = args[1:]
    if any(flag in _GIT_CONFIG_WRITE_FLAGS for flag in rest):
        return False
    positionals = [a for a in rest if not a.startswith("-")]
    return len(positionals) <= 1


_PROTECTED_BRANCHES = {"main", "master"}


def _git_dir() -> Path | None:
    """The git dir of the checkout this command runs in, or None when it isn't in one.

    Walks up from the invocation's cwd to the nearest `.git` — a directory in a normal checkout, a
    file holding `gitdir: <path>` in a linked worktree or submodule. Following the invocation rather
    than PROJECT_DIR is what makes per-checkout state (HEAD) correct inside a worktree, where
    CLAUDE_PROJECT_DIR still names the main checkout: borrowing that checkout's HEAD would answer
    confidently about a different branch, so no `.git` found answers nothing.
    """
    cwd = Path(_INVOCATION["cwd"])
    git = next((p / ".git" for p in (cwd, *cwd.parents) if (p / ".git").exists()), None)
    if git is None:
        return None
    if git.is_file():
        # `gitdir:` is relative for submodules and for worktrees created with --relative-paths, and
        # it is relative to the dir holding the `.git` file — `/` keeps an absolute path unchanged.
        git = git.parent / git.read_text(encoding="utf-8").strip().removeprefix("gitdir:").strip()
    return git


def _common_git_dir() -> Path | None:
    """The git dir holding shared state — refs and `packed-refs`, which worktrees don't get a copy of.

    A linked worktree's own gitdir carries a `commondir` file pointing back at the main one (`../..`);
    without it the dir is its own common dir.
    """
    git = _git_dir()
    if git is None:
        return None
    try:
        common = (git / "commondir").read_text(encoding="utf-8").strip()
    except OSError:
        return git
    return (git / common).resolve()


def _current_branch_name() -> str | None:
    """The checked-out branch name from HEAD, or None on a detached HEAD / unreadable git dir.

    HEAD holds `ref: refs/heads/<branch>` on a normal checkout; a detached HEAD holds a raw sha
    (no branch), and an unreadable git dir means we can't tell — both give None. The name is taken
    whole, slashes included (`feature/x`), so it can be handed to git/gh as-is.
    """
    try:
        git = _git_dir()
        content = (git / "HEAD").read_text(encoding="utf-8").strip() if git is not None else ""
    except OSError:
        return None
    if not content.startswith("ref:"):
        return None
    return content.partition("refs/heads/")[2] or None


def _current_branch_protected() -> bool:
    """True iff the repo's checked-out branch is main/master (None / detached HEAD → False)."""
    return _current_branch_name() in _PROTECTED_BRANCHES


def git_push_to_protected_branch(args: list[str]) -> bool:
    """True iff a `git push …` would update main/master on the remote.

    Explicit refspecs are read from the args — the destination is the part after `:` (so `HEAD:main`,
    `main`, and `:main` all count). A bare `git push` / `git push <remote>` pushes the current branch,
    so we consult `.git/HEAD`. `HEAD` as an explicit ref also means the current branch.
    """
    if not args or args[0] != "push":
        return False
    positionals = [a for a in args[1:] if not a.startswith("-")]
    refs = positionals[1:]  # positionals[0] is the remote; the rest are refspecs
    if not refs:
        return _current_branch_protected()
    for ref in refs:
        dst = ref.split(":")[-1]
        if dst.rsplit("/", 1)[-1] in _PROTECTED_BRANCHES:
            return True
        if dst == "HEAD" and _current_branch_protected():
            return True
    return False


def _branch_on_remote(name: str) -> bool:
    """True iff branch `name` has a remote-tracking ref (it's been pushed) — loose or packed.

    Reads `<git-dir>/refs/remotes/<remote>/<name>` and `packed-refs` (files, no subprocess). If the
    branch is on a remote, its commits are recoverable, so even a force-delete loses nothing.
    """
    git = _common_git_dir()
    if git is None:
        return False
    remotes_dir = git / "refs" / "remotes"
    if remotes_dir.is_dir():
        for remote in remotes_dir.iterdir():
            if (remote / name).exists():
                return True
    packed = git / "packed-refs"
    try:
        lines = packed.read_text(encoding="utf-8").splitlines()
    except OSError:
        return False
    prefix = "refs/remotes/"
    for line in lines:
        ref = line.split(" ", 1)[1].strip() if " " in line else ""
        if ref.startswith(prefix) and ref[len(prefix) :].partition("/")[2] == name:
            return True
    return False


def git_branch_force_delete(args: list[str]) -> bool:
    """True iff `git branch` force-deletes a branch that is NOT on any remote (so work could be lost).

    Force-delete (`-D`, or `-d`/`--delete` with `-f`/`--force`) drops a branch even with unmerged
    commits. If the branch is on a remote (pushed), the commits are recoverable, so we allow it — only
    an unpushed force-delete asks. A plain `-d`/`--delete` is safe regardless (git refuses to delete an
    unmerged branch) and falls through to the `branch` allow.
    """
    if not args or args[0] != "branch":
        return False
    flags = set(args[1:])
    forcing = "-D" in flags or (bool(flags & {"-d", "--delete"}) and bool(flags & {"-f", "--force"}))
    if not forcing:
        return False
    names = [a for a in args[1:] if not a.startswith("-")]
    return any(not _branch_on_remote(n) for n in names)


def any_path_under_git(args: list[str]) -> bool:
    """True iff any non-flag path arg resolves inside the repo's `.git/` dir (`.git` is off-limits).

    Blocks `cat .git/config`, `grep -r x .git/`, etc. — the agent inspects git via `git` commands,
    never by reading `.git/` files. `resolve()` collapses `..`, so a traversal in can't dodge it.
    """
    git_dir = (Path(PROJECT_DIR) / ".git").resolve()
    project_root = Path(PROJECT_DIR).resolve()
    for arg in args:
        if arg.startswith("-"):
            continue
        candidate = Path(arg) if Path(arg).is_absolute() else project_root / arg
        real = candidate.resolve()
        if real == git_dir or git_dir in real.parents:
            return True
    return False


# ── git branch creation: only off an up-to-date main/master ──────────────────

_BRANCH_CREATE_FLAGS = {"checkout": {"-b", "-B"}, "switch": {"-c", "-C"}}
# Flags that make `git branch …` manage existing branches (list/delete/move/copy/…) rather than
# create one from a start-point — when any is present, it's not a creation we gate.
_BRANCH_MGMT_FLAGS = {
    "-d",
    "-D",
    "--delete",
    "-m",
    "-M",
    "--move",
    "-c",
    "-C",
    "--copy",
    "--list",
    "-l",
    "-a",
    "--all",
    "-r",
    "--remotes",
    "-v",
    "-vv",
    "--show-current",
    "--edit-description",
    "--set-upstream-to",
    "-u",
    "--unset-upstream",
    "--contains",
    "--merged",
    "--no-merged",
    "--points-at",
}


class BranchBase(NamedTuple):
    """A branch-creating git command's intent: whether it creates, and its start-point ref."""

    creating: bool
    start_point: str | None  # explicit base ref, or None to root on the current HEAD


def _is_branch_creation_cmd(sub: str, rest: list[str]) -> bool:
    """True iff `<sub> <rest>` creates a branch: `checkout -b/-B`, `switch -c/-C`, or `branch <name>`."""
    if sub in _BRANCH_CREATE_FLAGS:
        return bool(set(rest) & _BRANCH_CREATE_FLAGS[sub])
    if sub == "branch":
        return not (set(rest) & _BRANCH_MGMT_FLAGS)
    return False


def _branch_base(args: list[str]) -> BranchBase:
    """The branch-creation intent of a git command.

    `creating` is False when the command doesn't create a branch. On creation, `start_point` is the
    explicit base ref if given (`git switch -c x main` → 'main'), else None (rooted on current HEAD).
    Covers `checkout -b/-B`, `switch -c/-C`, and `branch <name> [<base>]`.
    """
    if not args or not _is_branch_creation_cmd(args[0], args[1:]):
        return BranchBase(creating=False, start_point=None)
    rest = args[1:]
    positionals = [a for a in rest if not a.startswith("-")]
    if not positionals:  # bare `git branch` (list), or `checkout -b` with no name
        return BranchBase(creating=False, start_point=None)
    base = positionals[1] if len(positionals) > 1 else None
    return BranchBase(creating=True, start_point=base)


def _short_ref(ref: str) -> str:
    """Last segment of a ref name: 'origin/main' → 'main', 'refs/heads/x' → 'x'."""
    return ref.rsplit("/", 1)[-1]


def git_branch_off_protected(args: list[str]) -> bool:
    """True (→ allow + reminder) iff a git command creates a branch rooted on something other than main/master.

    An explicit start-point is judged by that ref (so `git switch -c x main` is clean even from a
    feature branch); with no start-point the branch roots on the current branch (read from HEAD).
    """
    creating, base = _branch_base(args)
    if not creating:
        return False
    if base is None:
        # Rooted on the current branch. Fail open when HEAD is unreadable (detached / worktree),
        # matching git_push_to_protected_branch — an explicit non-trunk base is still caught below.
        name = _current_branch_name()
        return name is not None and name not in _PROTECTED_BRANCHES
    return _short_ref(base) not in _PROTECTED_BRANCHES


def _ref_sha(ref: str) -> str | None:
    """SHA a ref points to, from `<git-dir>/<ref>` (loose) or `packed-refs`; None if absent.

    Pure file reads, no subprocess — the same line acl-hook holds in `_branch_on_remote`.
    """
    git = _common_git_dir()
    if git is None:
        return None
    try:
        text = (git / ref).read_text(encoding="utf-8").strip()
        if text:
            return text
    except OSError:
        pass
    try:
        lines = (git / "packed-refs").read_text(encoding="utf-8").splitlines()
    except OSError:
        return None
    for line in lines:
        if not line or line[0] in "#^":
            continue
        sha, _, name = line.partition(" ")
        if name.strip() == ref:
            return sha.strip()
    return None


def _protected_synced(branch: str) -> bool | None:
    """Local `<branch>` vs cached `origin/<branch>`: True=equal, False=diverged, None=can't tell.

    No fetch — compares the last-fetched remote ref. If either side is missing (never fetched, no
    remote), we can't tell, so None, and the caller doesn't block.
    """
    local = _ref_sha(f"refs/heads/{branch}")
    remote = _ref_sha(f"refs/remotes/origin/{branch}")
    if local is None or remote is None:
        return None
    return local == remote


def git_branch_off_stale_main(args: list[str]) -> bool:
    """True (→ allow + reminder) iff branching off a protected base whose local ref provably differs from origin.

    Fires only when the base IS main/master (a non-trunk base is reminded separately) and we can prove
    divergence; an unknown sync state (no cached remote ref) never fires.
    """
    creating, base = _branch_base(args)
    if not creating:
        return False
    if base is not None and "/" in base:
        # An explicit remote-tracking base (`git switch -c x origin/main`) IS the freshest ref we
        # know of — comparing the local branch against it says nothing about this command.
        return False
    branch = base if base is not None else _current_branch_name()
    if branch not in _PROTECTED_BRANCHES:
        return False
    return _protected_synced(branch) is False


# ── a branch whose PR already merged is finished: no more commits on it ──────

_GH_TIMEOUT_SECONDS = 10


def _branch_has_merged_pr(branch: str) -> bool:
    """True iff GitHub reports a merged PR whose head is `branch`.

    The one question no ref file can answer: a squash-merged branch is not an ancestor of trunk and
    GitHub keeps its remote ref, so nothing on disk says "this already landed". Hence the plugin's
    single subprocess + network call (see CLAUDE.md). Anything short of a clear "merged" answers
    False — no `gh`, repo not on GitHub, unauthenticated, offline, or slow must never block a commit
    — and logs why, because a guard that has quietly stopped firing looks exactly like a clean repo.
    """
    logger = logging.getLogger("acl_hook")
    cmd = ["gh", "pr", "list", "--head", branch, "--state", "merged", "--limit", "1", "--json", "headRefOid"]
    try:
        proc = subprocess.run(  # noqa: S603 — fixed argv, `branch` is a ref name read from HEAD
            cmd,
            cwd=_INVOCATION["cwd"],
            capture_output=True,
            text=True,
            timeout=_GH_TIMEOUT_SECONDS,
            check=False,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        # No `gh` on PATH raises FileNotFoundError; a hung gh raises TimeoutExpired.
        logger.info("merged_pr_lookup=skip branch=%s cause=%s", branch, type(exc).__name__)
        return False
    if proc.returncode != 0:
        logger.info(
            "merged_pr_lookup=skip branch=%s cause=gh_rc%d err=%s", branch, proc.returncode, proc.stderr.strip()[:200]
        )
        return False
    try:
        merged = json.loads(proc.stdout)
    except json.JSONDecodeError:
        logger.info("merged_pr_lookup=skip branch=%s cause=bad_json", branch)
        return False
    # A branch name outlives its PR — worktree names get recycled, so a fresh branch can carry the
    # name of a merged one. The branch is finished only while its tip is still the merged commit.
    return bool(merged) and merged[0]["headRefOid"] == _ref_sha(f"refs/heads/{branch}")


def git_write_on_merged_branch(args: list[str]) -> bool:
    """True iff `git commit` runs on a branch whose PR is already merged.

    Such a branch is finished: the merged PR never picks up new commits, so work committed there
    lands nowhere and quietly rots. Only `commit` is gated — with no new commit possible on the
    branch, a push can carry only what was committed before the merge, and gating pushes too would
    spend a GitHub round-trip on every ordinary feature push. main/master and an unreadable HEAD are
    skipped (no PR of their own to be merged).
    """
    if not args or args[0] != "commit":
        return False
    branch = _current_branch_name()
    if branch is None or branch in _PROTECTED_BRANCHES:
        return False
    return _branch_has_merged_pr(branch)


# `checkout` flags that mean "make a ref current", never "copy files over the working tree".
_CHECKOUT_REF_FLAGS = {"-b", "-B", "--orphan", "--detach", "--track", "--no-track", "-t"}


def _is_worktree_path(arg: str) -> bool:
    """True iff this positional names something on disk — the tell that `checkout` is in path mode.

    `git checkout <thing>` is a branch switch or a file overwrite depending only on what `<thing>` is,
    and git resolves the ambiguity by looking for the path first. So do we: a name that exists in the
    tree is a path (`.`, `src/`, `app/main.py`), a name that does not is a ref. Paths are resolved
    from the invocation's cwd, which is where git would resolve them too.
    """
    return (Path(_INVOCATION["cwd"]) / arg).exists()


def git_discards_worktree_changes(args: list[str]) -> bool:
    """True iff a `git checkout`/`git restore` overwrites working-tree files with a committed version.

    That overwrite is the one git operation with no undo of any kind: what it destroys was never
    committed and never stashed, so there is no reflog entry and no dangling object to recover from.

    Ref mode is not this and stays allowed — `git checkout <branch>`, `-b`, a sha — because git
    refuses a switch that would clobber a modified file. `git restore --staged <path>` only rewrites
    the index, leaving the file on disk untouched, so it stays allowed too; `--worktree` alongside it
    puts the overwrite back.
    """
    if not args:
        return False
    sub, rest = args[0], args[1:]
    if sub == "restore":
        staged_only = bool({"--staged", "-S"} & set(rest)) and not ({"--worktree", "-W"} & set(rest))
        return not staged_only
    if sub != "checkout" or set(rest) & _CHECKOUT_REF_FLAGS:
        return False
    if "--" in rest:
        return bool(rest[rest.index("--") + 1 :])
    return any(_is_worktree_path(a) for a in rest if not a.startswith("-"))


_GCS_COPY_SUBCOMMANDS = (["storage", "cp"], ["storage", "rsync"])
_GCS_COPY_MIN_POSITIONALS = 2  # source + destination


def gcloud_storage_download(args: list[str]) -> bool:
    """True iff `gcloud storage cp/rsync` copies FROM a bucket TO this machine — a read, not a write.

    The destination is the last positional. A `gs://` destination is an upload or a bucket-to-bucket
    copy: that mutates remote state and stays an `ask`. Anything else lands on local disk, where the
    fs rules already apply, so it's as safe as `gcloud storage cat`.
    """
    if args[:2] not in _GCS_COPY_SUBCOMMANDS:
        return False
    positionals = [a for a in args[2:] if not a.startswith("-")]
    return len(positionals) >= _GCS_COPY_MIN_POSITIONALS and not positionals[-1].startswith("gs://")


CUSTOM_FNS: dict[str, Callable[[list[str]], bool]] = {
    "gcloud_storage_download": gcloud_storage_download,
    "curl_mutating_remote": curl_mutating_remote,
    "sed_inline_long": sed_inline_long,
    "rm_recursive": rm_recursive,
    "all_paths_inside_project": all_paths_inside_project,
    "all_paths_under_scratch": all_paths_under_scratch,
    "git_config_read": git_config_read,
    "git_push_to_protected_branch": git_push_to_protected_branch,
    "git_branch_force_delete": git_branch_force_delete,
    "git_branch_off_protected": git_branch_off_protected,
    "git_branch_off_stale_main": git_branch_off_stale_main,
    "git_write_on_merged_branch": git_write_on_merged_branch,
    "git_discards_worktree_changes": git_discards_worktree_changes,
    "any_path_under_git": any_path_under_git,
    "tee_writes_outside_project": tee_writes_outside_project,
}


def check_rule(rule: Rule, cmd_args: list[str]) -> bool:
    """Dispatch a single ACL rule to the appropriate matcher / predicate."""
    if "fn" in rule:
        return CUSTOM_FNS[rule["fn"]](cmd_args)
    if "args" in rule:
        return matches_args(rule["args"], cmd_args)
    if "args_contain" in rule:
        return matches_args_contain(rule["args_contain"], cmd_args)
    if "args_glob" in rule:
        return matches_args_glob(rule["args_glob"], cmd_args)
    return False


# ── Top-level antipattern detectors (operate on bashlex ASTs) ────────────────


WalkItem = tuple[BashNode, "BashNode | None", "int | None"]


def _walk_with_parent(
    node: BashNode,
    parent: BashNode | None = None,
    position: int | None = None,
) -> Iterator[WalkItem]:
    yield node, parent, position
    for idx, child in enumerate(_node_children(node)):
        yield from _walk_with_parent(child, parent=node, position=idx)


def _node_children(node: BashNode) -> Iterator[BashNode]:
    parts = getattr(node, "parts", None)
    if parts:
        yield from parts
    list_children = getattr(node, "list", None)
    if list_children:
        yield from list_children
    cmd_child = getattr(node, "command", None)
    if cmd_child is not None:
        yield cmd_child


def _command_words(node: BashNode) -> list[str]:
    if getattr(node, "kind", None) != "command":
        return []
    return [part.word for part in (getattr(node, "parts", []) or []) if getattr(part, "kind", None) == "word"]


def has_function_def(trees: Iterable[BashNode]) -> bool:
    """True iff any tree contains a Bash function definition."""
    for tree in trees:
        for node, _parent, _pos in _walk_with_parent(tree):
            if getattr(node, "kind", None) == "function":
                return True
    return False


def _redirect_targets(trees: Iterable[BashNode]) -> Iterator[str]:
    """Every filename a `>` / `>>` in these trees writes to.

    `2>&1` carries an int on `output` and `> $LOG` a non-literal word; neither names a path we
    can vet, and both are left to the other gates rather than guessed at.
    """
    for tree in trees:
        for node, _parent, _pos in _walk_with_parent(tree):
            if getattr(node, "kind", None) != "redirect":
                continue
            output = getattr(node, "output", None)
            word = getattr(output, "word", None)
            if isinstance(word, str) and "$" not in word:
                yield word


def redirect_escapes_project(trees: Iterable[BashNode]) -> bool:
    """True iff a redirect writes outside the project, or into its `.git`.

    The hole this closes: every rule in `acl.json` judges the *command*, so `rm README.md` is
    denied while `echo x > README.md` — same file, gone the same way — was allowed, and
    `echo x > ~/.ssh/authorized_keys` with it. A redirect is a write; it gets the same
    boundary the Write tool has.
    """
    return any(write_escapes_project(target) for target in _redirect_targets(trees))


def _c_arg(words: list[str]) -> str | None:
    """The token following the first `-c` flag in a command word list, if present."""
    for i, word in enumerate(words):
        if word == "-c" and i + 1 < len(words):
            return words[i + 1]
    return None


def python_c_not_after_pipe(trees: Iterable[BashNode]) -> bool:
    """True iff a `python[3] -c …` script should be denied: standalone (not a pipe receiver) AND long.

    A pipe filter (`<cmd> | python3 -c "…"`) is always allowed. A standalone `python3 -c` is allowed
    only when its script is a single line ≤ PYTHON_C_INLINE_MAX — the import/version introspection
    the agent needs. Longer or multiline scripts are denied: hidden in one opaque arg they bypass
    the size/line gates and aren't reviewable, so they belong in a file.
    """
    for tree in trees:
        for node, parent, position in _walk_with_parent(tree):
            words = _command_words(node)
            if not words or Path(words[0]).name not in ("python", "python3"):
                continue
            if "-c" not in words[1:]:
                continue
            if parent is not None and getattr(parent, "kind", None) == "pipeline" and (position or 0) > 0:
                continue
            script = _c_arg(words)
            if script is not None and "\n" not in script and len(script) <= PYTHON_C_INLINE_MAX:
                continue
            return True
    return False


_LOOP_RESERVED_WORDS = {"until", "while", "for"}


def wait_loop_unbounded(trees: Iterable[BashNode]) -> bool:
    """True iff a loop (until/while/for) body contains a `sleep` — a poll with no upper time bound.

    A bare poll loop runs until its condition trips; if it never does (failed deploy, wrong target)
    it hangs forever. We do NOT deny it — that contradicts the harness, which recommends until-loops
    (the bug that dropped the old `until_loop_with_sleep`/`chained_sleep` detectors). Instead main()
    transparently wraps it in `timeout` via updatedInput: no prompt, no block, agent unaware. An
    unbounded background loop is a leak, which IS this plugin's scope ("we only ACL for damage/leak").
    A loop already wrapped in `timeout … bash -c '…'` hides its body inside a quoted word, so bashlex
    never yields these nodes and this returns False — the wrap is idempotent for free.
    """
    for tree in trees:
        has_loop = has_sleep = False
        for node, _parent, _pos in _walk_with_parent(tree):
            if getattr(node, "kind", None) == "reservedword" and getattr(node, "word", "") in _LOOP_RESERVED_WORDS:
                has_loop = True
            words = _command_words(node)
            if words and Path(words[0]).name == "sleep":
                has_sleep = True
            if has_loop and has_sleep:
                return True
    return False


# ── Per-command ACL check (split into helpers to keep complexity bounded) ────

_PROC_WRAPPERS = ("time", "nohup", "nice", "setsid", "stdbuf", "ionice", "taskset")
_WRAPPER_FLAGS_WITH_VALUE = ("-n", "-c", "-p")
_TIMEOUT_FLAGS_WITH_VALUE = ("-s", "--signal", "-k", "--kill-after")


def _strip_env_assignments(parts: list[str]) -> list[str]:
    while parts and "=" in parts[0] and not parts[0].startswith("-"):
        parts = parts[1:]
    return parts


def _strip_wrapper(parts: list[str]) -> list[str]:
    while parts and parts[0] in _PROC_WRAPPERS:
        parts = parts[1:]
        while parts and parts[0].startswith("-"):
            parts = parts[2:] if parts[0] in _WRAPPER_FLAGS_WITH_VALUE and len(parts) > 1 else parts[1:]
    return parts


def _strip_timeout(parts: list[str]) -> list[str]:
    if not parts or parts[0] != "timeout":
        return parts
    parts = parts[1:]
    while parts and parts[0].startswith("-"):
        parts = parts[2:] if parts[0] in _TIMEOUT_FLAGS_WITH_VALUE and len(parts) > 1 else parts[1:]
    if parts:  # consume the <duration> positional
        parts = parts[1:]
    return parts


_SHELL_CMDS = {"bash", "sh"}
_SHELL_C_PARTS = 3  # exactly `<shell> -c <script>` after stripping env/wrapper/timeout


def _extract_shell_c(command: str) -> str | None:
    """Return the script of a verifiable `bash -c '<script>'` / `sh -c '<script>'`, else None.

    Only the exact `[env] [wrapper] [timeout] <shell> -c <script>` shape with a fully-literal script
    is recursed into — the ACL re-checks the script as if typed directly (so `bash -c 'rm -rf /etc'`
    is denied, `bash -c 'git status'` allowed). Any expansion (`$…`, backtick) is non-literal: its
    runtime value can't be statically vetted, so we return None and let the blanket `bash` deny
    stand. Other forms (`bash -lc`, extra args, `bash file.sh`) also fall through to deny.
    """
    try:
        parts = _strip_timeout(_strip_wrapper(_strip_env_assignments(shlex.split(command))))
    except ValueError:
        return None
    if len(parts) != _SHELL_C_PARTS or parts[1] != "-c" or Path(parts[0]).name not in _SHELL_CMDS:
        return None
    script = parts[2]
    if "$" in script or "`" in script:
        return None
    return script


def _is_claude_script(command: str) -> bool:
    return (
        fnmatch(command, ".claude/skills/*/*.py")
        or fnmatch(command, "*/.claude/skills/*/*.py")
        or fnmatch(command, "*/.claude/hooks/*.py")
    )


def _venv_bin_deny_reason(command: str) -> str | None:
    if "/" not in command:
        return None
    abs_command = (Path(command) if Path(command).is_absolute() else Path(PROJECT_DIR) / command).resolve()
    venv_bin = (Path(PROJECT_DIR) / ".venv" / "bin").resolve()
    if venv_bin in abs_command.parents:
        bare = abs_command.name
        return (
            f"Don't invoke `{command}` — call `{bare}` directly. The project venv should be active "
            f"in the shell profile.\nIf `{bare}` still fails, ASK THE USER to activate the venv "
            f"(`source .venv/bin/activate` in their terminal). Workarounds like `source`, `.`, "
            f"`bash -c`, invoking the venv binary by path — all blocked."
        )
    return None


def _python_path_deny_reason(command: str) -> str | None:
    if "/" in command and Path(command).name in ("python", "python3"):
        return (
            "Use python3 directly, not a path. The project venv should be active in the shell profile.\n"
            "If `python3` runs from /usr/bin (venv not active), ASK THE USER to activate it."
        )
    return None


_UNKNOWN_CMD_REASON = (
    "Unknown command `{cmd}` — not in ACL. Don't smuggle it through a wrapper or a clever "
    "one-liner. Use a simpler primitive that IS in the allow-list (ls/cat/grep/find/git/gh/…), "
    "or split into multiple Bash calls. If you genuinely need this command, ask the user to "
    "add it to ACL."
)
_SHLEX_ERROR_REASON = (
    "Bash command failed to parse ({err}). Rewrite as a simpler primitive the shell can "
    "parse cleanly, or split into multiple Bash calls."
)

Decision = tuple[str, str, str]


def _preflight(command: str) -> Decision | None:
    """Per-command early decisions (allow claude scripts, deny venv paths) before ACL lookup."""
    if _is_claude_script(command):
        return "allow", "", "claude_script"
    venv = _venv_bin_deny_reason(command)
    if venv is not None:
        return "deny", venv, "venv_bin"
    py = _python_path_deny_reason(command)
    if py is not None:
        return "deny", py, "python_path"
    return None


def _apply_acl(command: str, args: list[str]) -> Decision:
    """Walk the ACL rules for `command`, falling back to its `default`."""
    entry = acl()[command]
    for rule in entry.get("rules", []):
        if check_rule(rule, args):
            return rule["decision"], rule.get("reason", ""), "rule"
    default = entry["default"]
    return default, entry.get("reason", ""), f"default:{default}"


def check_command(cmd_str: str, logger: logging.Logger, *, agent_type: str) -> Decision:
    """Check a single command against ACL. Returns (decision, reason, log_detail)."""
    script = _extract_shell_c(cmd_str)
    if script is not None:
        # `bash -c '<literal>'`: re-run the full pipeline on the script, as if it were typed directly.
        verdict, reason = _decide(script, logger, agent_type)
        logger.info('decision=%s command="%s" matched=shell_c_recurse agent=%s', verdict, for_log(cmd_str), agent_type)
        return verdict, reason, "shell_c_recurse"
    decision = _classify(cmd_str)
    verdict, _, detail = decision
    logger.info('decision=%s command="%s" matched=%s agent=%s', verdict, for_log(cmd_str), detail, agent_type)
    return decision


def _classify(cmd_str: str) -> Decision:
    """Pure classification: no logging side effects, so the logic stays linear."""
    try:
        parts = shlex.split(cmd_str)
    except ValueError as e:
        return "deny", _SHLEX_ERROR_REASON.format(err=e), "shlex_error"

    parts = _strip_timeout(_strip_wrapper(_strip_env_assignments(parts)))
    if not parts or parts[0].startswith("#"):
        return "allow", "", "comment" if parts and parts[0].startswith("#") else "empty command"

    command = parts[0]
    args = [expand_home(a) for a in parts[1:]]

    preflight = _preflight(command)
    if preflight is not None:
        return preflight

    table = acl()
    # Basename normalization so /usr/bin/git is ACL'd the same as bare git.
    if "/" in command and Path(command).name in table:
        command = Path(command).name

    if command not in table:
        return "deny", _UNKNOWN_CMD_REASON.format(cmd=command), "unknown_command"

    return _apply_acl(command, args)


# ── main() and its emit helpers ──────────────────────────────────────────────


def _emit(decision: str, reason: str) -> None:
    # On `allow`, `permissionDecisionReason` is user-facing only and never reaches the agent (verified
    # empirically) — so a reminder on an `allow` rule is delivered via `additionalContext`, the one
    # allow channel the agent actually sees. deny/ask keep using `permissionDecisionReason`.
    hook_output: dict[str, object] = {
        "hookEventName": "PreToolUse",
        "permissionDecision": decision,
        "permissionDecisionReason": "" if decision == "allow" else reason,
    }
    if decision == "allow" and reason:
        hook_output["additionalContext"] = reason
    sys.stdout.write(json.dumps({"hookSpecificOutput": hook_output}) + "\n")


def _emit_rewrite(tool_input: dict[str, object], new_command: str) -> None:
    """Emit `allow` while transparently replacing the command — no prompt, and no hook re-trigger."""
    sys.stdout.write(
        json.dumps(
            {
                "hookSpecificOutput": {
                    "hookEventName": "PreToolUse",
                    "permissionDecision": "allow",
                    "permissionDecisionReason": "",
                    "updatedInput": {**tool_input, "command": new_command},
                }
            }
        )
        + "\n"
    )


def _log_deny(logger: logging.Logger, command: str, agent_type: str, tag: str) -> None:
    logger.info('decision=deny command="%s" matched=%s agent=%s', for_log(command), tag, agent_type)


_TOO_LARGE_REASON = (
    "Bash command too large ({n} chars / {lines} lines; limit {mlen}/{mlines}). SPLIT into "
    "several simple Bash calls — each step gets its own ACL check and feedback. Antipatterns "
    "to avoid: long `for x in …; do …; done`, function defs `name() {{…}}`, `&&` chains "
    'longer than 3 links, `python -c "<multiline script>"`. Genuinely atomic script with '
    "control flow (rare) → Write tool to a file, then run it."
)
_HEREDOC_REASON = "Agents cannot use heredoc (<<) in Bash — use the Write tool instead."
_BASHLEX_REASON = (
    "Bash command failed to parse via bashlex ({errname}): {err}. This blocks the AST-based "
    "antipattern detectors from checking it, so we fail closed. Likely cause: ANSI-C escapes "
    "(`$'…'`), process substitution (`<(…)` / `>(…)`), unbalanced quotes. Rewrite as a "
    "simpler primitive or split into multiple Bash calls."
)
_FUNCTION_DEF_REASON = (
    "Bash function definitions (`name() { … }`) inside a Bash call are denied — split into "
    "multiple simple Bash calls. If you need reusable logic, Write it as a script file."
)
_PYTHON_C_REASON = (
    f"`python -c` standalone is allowed only as a short single-line check (≤{PYTHON_C_INLINE_MAX} "
    'chars), or as a pipe filter (`<cmd> | python3 -c "…"`). This script is longer/multiline: '
    "hidden in one arg it bypasses the size gate and isn't reviewable. Options: (1) pipe data in; "
    "(2) Write the script to a file and run it; (3) split into simple Bash builtins or `jq`."
)
_REDIRECT_REASON = (
    "A `>` / `>>` outside the project is a write to the user's machine, and no rule here reads "
    "redirect targets — so `echo x > ~/.bashrc` would land unchecked while `rm` on the same file "
    "is denied. Write inside the project (`.scratch/` for throwaway output), or to `/tmp/` if it "
    "must be out-of-tree. `.git/` is off-limits: use git commands. If the user's own dotfile "
    "genuinely has to change, hand them the line to run."
)
_AST_DETECTORS: list[tuple[Callable[[Iterable[BashNode]], bool], str, str]] = [
    (has_function_def, _FUNCTION_DEF_REASON, "function_def"),
    (python_c_not_after_pipe, _PYTHON_C_REASON, "python_c_standalone"),
    (redirect_escapes_project, _REDIRECT_REASON, "redirect_outside_project"),
]


Verdict = tuple[str, str]


def _size_gate(command: str, logger: logging.Logger, agent_type: str) -> Verdict | None:
    """Deny commands that are too long or span too many lines."""
    line_count = command.count("\n") + 1
    if len(command) <= MAX_BASH_LEN and line_count <= MAX_BASH_LINES:
        return None
    logger.info("decision=deny command_too_long len=%d lines=%d agent=%s", len(command), line_count, agent_type)
    return "deny", _TOO_LARGE_REASON.format(n=len(command), lines=line_count, mlen=MAX_BASH_LEN, mlines=MAX_BASH_LINES)


def _heredoc_gate(command: str, logger: logging.Logger, agent_type: str) -> Verdict | None:
    """Deny heredoc usage; agents must use the Write tool for multiline content."""
    if "<<" not in command:
        return None
    _log_deny(logger, command, agent_type, "agent_heredoc")
    return "deny", _HEREDOC_REASON


def _ast_gate(command: str, logger: logging.Logger, agent_type: str) -> Verdict | None:
    """Parse with bashlex; fail closed on parse errors, then run AST antipattern detectors."""
    try:
        trees = bashlex.parse(command)
    except Exception as e:  # noqa: BLE001 — bashlex raises a variety; fail closed
        _log_deny(logger, command, agent_type, f"bashlex_parse_failed:{type(e).__name__}")
        return "deny", _BASHLEX_REASON.format(errname=type(e).__name__, err=e)
    for detector, reason, tag in _AST_DETECTORS:
        if detector(trees):
            _log_deny(logger, command, agent_type, tag)
            return "deny", reason
    return None


_GATES = (_size_gate, _heredoc_gate, _ast_gate)


def _resolve_chained(command: str, logger: logging.Logger, agent_type: str) -> Verdict:
    """Run ACL on each sub-command and keep the strictest decision (deny > ask > allow).

    An allow-level reminder (an `allow` rule whose non-empty reason becomes agent `additionalContext`)
    survives when nothing stricter fires: the first such reminder is carried on the final allow.
    """
    final: Verdict = ("allow", "")
    for sub_cmd in split_chained_commands(command):
        decision, reason, _ = check_command(sub_cmd, logger, agent_type=agent_type)
        if DECISION_PRIORITY[decision] > DECISION_PRIORITY[final[0]]:
            final = (decision, reason)
        elif decision == "allow" and final[0] == "allow" and reason and not final[1]:
            final = ("allow", reason)
    return final


def _decide(command: str, logger: logging.Logger, agent_type: str) -> Verdict:
    for gate in _GATES:
        verdict = gate(command, logger, agent_type)
        if verdict is not None:
            return verdict
    return _resolve_chained(command, logger, agent_type)


def _link_starts_with_timeout(link: str) -> bool:
    try:
        parts = _strip_wrapper(_strip_env_assignments(shlex.split(link)))
    except ValueError:
        return False
    return bool(parts) and Path(parts[0]).name == "timeout"


def _has_timeout_prefix(command: str) -> bool:
    """True iff every link of `command` already runs under a leading `timeout` — the agent bounded it itself.

    Every link, not just the first: `timeout 60 gh pr checks 12; tail -f app.log` bounds the check
    and leaves the tail running forever, so one bounded link must not exempt the rest. The cost of
    reading it strictly is a chain like `cd repo && timeout 7200 npm run dev`, which gets the outer
    bound anyway — write it as `timeout 7200 bash -c 'cd repo && npm run dev'` to keep the hatch.
    """
    return all(_link_starts_with_timeout(link) for link in split_chained_commands(command))


def _wrap_timeout(seconds: int, command: str) -> str:
    """`command` bounded by GNU `timeout`.

    `-v` is what keeps the bound honest: on expiry timeout prints `sending signal TERM to command`
    on stderr, so a job cut short reads as cut short. Without it a killed `until … done` ends with
    the same silence as one whose condition tripped, and the agent concludes the wait succeeded.

    A command that literally starts with `bash -c` / `sh -c` is only prefixed — re-wrapping nests a
    second layer of quote escaping for no gain. Everything else is quoted whole, including a shell
    behind an env assignment (`FOO=1 bash -c '…'`): prefixing there would hand `timeout` the
    assignment as its program name, and the rewrite dies with exit 127 blaming the agent's own var.
    """
    if shlex.split(command)[:2] in (["bash", "-c"], ["sh", "-c"]):
        return f"timeout -v {seconds} {command}"
    return f"timeout -v {seconds} bash -c {shlex.quote(command)}"


class BoundedCommand(NamedTuple):
    """A command rewritten to run under `timeout`, with the log tag naming the rule that asked for it."""

    command: str
    matched: str


def unbounded_wait_loop(command: str) -> bool:
    """True iff `command` polls in a loop without ever saying how long it is willing to wait.

    An explicit leading `timeout …` is the hatch: it turns the poll into a bounded wait, which is
    the shape the deny asks for. `bash -c '<literal>'` is unwrapped first so smuggling the loop
    through a shell doesn't dodge the check — the same recursion `check_command` does.
    """
    if _has_timeout_prefix(command):
        return False
    script = _extract_shell_c(command)
    return wait_loop_unbounded(bashlex.parse(script if script is not None else command))


def _bound(command: str, *, background: bool) -> BoundedCommand | None:
    """The bounded rewrite of a detached `command`, or None when it already carries a bound.

    Reached only for an otherwise-`allow` command, so bashlex already parsed it cleanly. An explicit
    leading `timeout …` wins: it's the agent's hatch for a job that genuinely needs hours, and it's
    what makes the wrap idempotent.
    """
    if not background or _has_timeout_prefix(command):
        return None
    return BoundedCommand(_wrap_timeout(BACKGROUND_TIMEOUT_SECONDS, command), "background_unbounded")


_WAIT_LOOP_REASON = (
    "A poll loop that never says how long it will wait is denied — `until <cond>; do sleep N; done` "
    "keeps going until the condition trips, and when it never does (the job already exited, the "
    "deploy failed, the path is wrong) it spends the whole tool timeout and fills your context with "
    "`tail` output. Three ways out, best first:\n"
    "1. Don't wait at all: run the slow command itself with `run_in_background: true` — it keeps "
    "running across turns and re-invokes you when it exits.\n"
    "2. Waiting on a condition is what the Monitor tool is for; use it instead of a shell loop.\n"
    "3. If you truly must poll from Bash, decide up front how long you are willing to wait and put "
    "that in the command: `timeout -v 300 bash -c 'until <cond>; do sleep 10; done'`. An explicit "
    "`timeout` prefix is always accepted, and `-v` makes a wait that was cut short say so instead of "
    "ending in the same silence as one that succeeded."
)


# ── Autonomous mode: `ask` becomes `deny` ────────────────────────────────────
#
# Set ACL_HOOK_AUTONOMOUS=1 when Claude Code runs with nobody at the keyboard (`claude -p`, cron,
# CI). There an `ask` can't be answered — it either hangs or is auto-refused with no explanation —
# so we turn it into a deny that tells the agent why and what to do instead.

_AUTONOMOUS_ENV = "ACL_HOOK_AUTONOMOUS"
_AUTONOMOUS_ON = {"1", "true", "yes"}
_AUTONOMOUS_REASON = (
    f"Autonomous mode ({_AUTONOMOUS_ENV}=1): no human is at the keyboard to approve this, so a "
    "command that would normally prompt is denied. Find a route that doesn't need approval, or "
    "finish the rest of the task and report this command for the user to run themselves."
)


def _autonomous() -> bool:
    return os.environ.get(_AUTONOMOUS_ENV, "").strip().lower() in _AUTONOMOUS_ON


def _judge(command: str, tool_input: dict[str, object], agent_type: str, logger: logging.Logger) -> None:
    """Decide `command` and emit the verdict (or a bounded rewrite), logging one `final=` line."""
    ensure_scratch_dir()
    decision, reason = _decide(command, logger, agent_type)
    if decision == "allow" and unbounded_wait_loop(command):
        decision, reason = "deny", _WAIT_LOOP_REASON
        logger.info('decision=deny command="%s" matched=wait_loop_unbounded agent=%s', for_log(command), agent_type)
    if decision == "allow":
        bounded = _bound(command, background=bool(tool_input.get("run_in_background")))
        if bounded is not None:
            logger.info('final=rewrite command="%s" matched=%s agent=%s', for_log(command), bounded.matched, agent_type)
            _emit_rewrite(tool_input, bounded.command)
            return
    if decision == "ask" and _autonomous():
        decision = "deny"
        reason = f"{reason}\n\n{_AUTONOMOUS_REASON}" if reason else _AUTONOMOUS_REASON
        logger.info('decision=deny command="%s" matched=autonomous_ask_denied agent=%s', for_log(command), agent_type)
    logger.info('final=%s command="%s" agent=%s', decision, for_log(command), agent_type)
    _emit(decision, reason)


def main() -> None:
    """PreToolUse entry point: read stdin payload, emit allow/ask/deny (or a bounded rewrite).

    Every invocation leaves a trail: a `received` line before any work, then exactly one `final=`
    line (or `final=error` + traceback if the hook itself dies). A command missing from the log
    means the hook never ran at all — check that the plugin is enabled.
    """
    logger = setup_logging()
    data = json.loads(sys.stdin.read())
    _INVOCATION["cwd"] = str(data.get("cwd") or PROJECT_DIR)
    tool_name = data.get("tool_name")
    tool_input = data.get("tool_input", {}) if tool_name == "Bash" else {}
    command = tool_input.get("command", "")
    agent_type = data.get("agent_type") if data.get("agent_id") is not None else "main"
    if not command:
        logger.info("final=skip tool=%s agent=%s", tool_name, agent_type)
        return
    logger.info('received command="%s" agent=%s', for_log(command), agent_type)
    try:
        _judge(command, tool_input, agent_type, logger)
    except Exception:
        # A crash makes Claude Code fall back to prompting with no explanation — the one case where
        # the user sees a question and the log would otherwise be silent. Record it, then re-raise.
        logger.exception('final=error command="%s" agent=%s', for_log(command), agent_type)
        raise


if __name__ == "__main__":
    main()
