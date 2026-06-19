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
import sys
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

# ── ACL config: bundled defaults auto-installed into the project on first run ──
#
# The full rule table lives in `acl_default.json` next to this file. On first
# invocation in a project, that file is copied to `$CLAUDE_PROJECT_DIR/.claude/
# acl.json` so the user can edit rules per-project without forking the plugin.
# Subsequent invocations read the project copy.

_BUNDLED_ACL_PATH = Path(__file__).parent / "acl_default.json"
_PLUGIN_JSON_PATH = Path(__file__).resolve().parent.parent / ".claude-plugin" / "plugin.json"
_PROJECT_ACL_RELPATH = Path(".claude") / "acl.json"
_SYNC_STAMP_RELPATH = Path(".claude") / ".acl-synced-version"
_ACL_CACHE: dict[str, Entry] | None = None


def project_acl_path() -> Path:
    """Where the per-project ACL config lives (auto-installed from bundled default)."""
    return Path(PROJECT_DIR) / _PROJECT_ACL_RELPATH


def _plugin_version() -> str:
    return str(json.loads(_PLUGIN_JSON_PATH.read_text(encoding="utf-8"))["version"])


def _rule_sig(rule: Rule) -> str:
    """Opaque dedup key for a rule: matcher kind + value + decision."""
    for kind in ("args", "args_contain", "args_glob", "fn"):
        if kind in rule:
            return f"{kind}\0{json.dumps(rule[kind], sort_keys=True)}\0{rule['decision']}"  # type: ignore[literal-required]
    return f"\0\0{rule['decision']}"


class MergeResult(NamedTuple):
    """Outcome of merging bundled defaults into a project ACL."""

    added: list[str]
    drifted: list[str]


def _merge_defaults(project: dict[str, Entry], bundled: dict[str, Entry]) -> MergeResult:
    """Add command keys the project entirely lacks; report (don't touch) drifted entries.

    Only wholly-missing command keys are added — a key the project already has is left alone,
    because we can't tell a deliberate override (e.g. `git` set to allow-all) from a stale copy.
    For existing keys whose bundled rule-set the project is missing, we return the names for an
    informational log so drift is visible instead of silent; the user re-syncs those by hand.
    Returns (added_keys, drifted_keys).
    """
    added: list[str] = []
    drifted: list[str] = []
    for name, b_entry in bundled.items():
        if name not in project:
            project[name] = b_entry
            added.append(name)
            continue
        seen = {_rule_sig(r) for r in project[name].get("rules", [])}
        if any(_rule_sig(r) not in seen for r in b_entry.get("rules", [])):
            drifted.append(name)
    return MergeResult(added, drifted)


def _sync_project_acl(target: Path, loaded: dict[str, Entry], version: str) -> None:
    """On a plugin version bump, add new default command keys to the project ACL.

    Additive only — never rewrites an existing command's rules, so project overrides win.
    """
    stamp = target.parent / _SYNC_STAMP_RELPATH.name
    if stamp.exists() and stamp.read_text(encoding="utf-8").strip() == version:
        return
    bundled: dict[str, Entry] = json.loads(_BUNDLED_ACL_PATH.read_text(encoding="utf-8"))
    added, drifted = _merge_defaults(loaded, bundled)
    log = logging.getLogger("acl_hook")
    if added:
        target.write_text(json.dumps(loaded, indent=2) + "\n", encoding="utf-8")
        log.info("acl_migrated version=%s added_commands=%s", version, added)
    if drifted:
        log.info("acl_drift version=%s commands_missing_default_rules=%s (re-sync by hand)", version, drifted)
    stamp.write_text(version, encoding="utf-8")


def _load_acl() -> dict[str, Entry]:
    """Read the project ACL, installing the bundled default and syncing new defaults on version bump."""
    global _ACL_CACHE  # noqa: PLW0603 — module-level cache for the parsed config
    if _ACL_CACHE is not None:
        return _ACL_CACHE
    target = project_acl_path()
    version = _plugin_version()
    if not target.exists():
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(_BUNDLED_ACL_PATH.read_text(encoding="utf-8"), encoding="utf-8")
        (target.parent / _SYNC_STAMP_RELPATH.name).write_text(version, encoding="utf-8")
    loaded: dict[str, Entry] = json.loads(target.read_text(encoding="utf-8"))
    _sync_project_acl(target, loaded, version)
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


def setup_logging() -> logging.Logger:
    """Initialise the rotating file logger used by every ACL decision."""
    log_dir = Path(HOME) / ".claude" / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    logger = logging.getLogger("acl_hook")
    logger.setLevel(logging.INFO)
    handler = RotatingFileHandler(log_dir / "acl-hook.log", maxBytes=5_000_000, backupCount=5)
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
    """Yield every top-level `&&` / `;` / `|` outside quotes as a Span."""
    in_single = in_double = False
    i = 0
    while i < len(command):
        c = command[i]
        if c == "'" and not in_double:
            in_single = not in_single
        elif c == '"' and not in_single:
            in_double = not in_double
        elif not (in_single or in_double):
            if c in {"|", ";"}:
                yield Span(i, i + 1)
            elif c == "&" and command[i + 1 : i + 2] == "&":
                yield Span(i, i + 2)
                i += 1
        i += 1


def split_chained_commands(command: str) -> list[str]:
    """Split a Bash command on top-level `&&`, `;`, `|` respecting quotes."""
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
# Cap an unbounded poll loop so a condition that never trips can't hang forever (foreground tool
# timeout maxes at 600s; a background loop has no such cap, so this is the real guard). Tunable.
WAIT_TIMEOUT_SECONDS = 600


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


def _current_branch_protected() -> bool:
    """True iff the repo's checked-out branch is main/master, read from `.git/HEAD` (no subprocess).

    `.git/HEAD` holds `ref: refs/heads/<branch>` on a normal checkout; a detached HEAD holds a raw
    sha (no branch, not protected). If `.git` is a file (worktree/submodule) or unreadable we can't
    tell, so we return False — the explicit-arg forms still catch a deliberate `git push origin main`.
    """
    try:
        content = (Path(PROJECT_DIR) / ".git" / "HEAD").read_text(encoding="utf-8").strip()
    except OSError:
        return False
    return content.startswith("ref:") and content.rsplit("/", 1)[-1] in _PROTECTED_BRANCHES


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


def git_branch_force_delete(args: list[str]) -> bool:
    """True iff `git branch` force-deletes — `-D`, or a `-d`/`--delete` combined with `-f`/`--force`.

    Force-delete drops a branch even with unmerged commits (work loss), so it's the one branch-delete
    worth confirming. A plain `-d`/`--delete` is safe — git refuses to delete an unmerged branch — so
    it falls through to the `branch` allow.
    """
    if not args or args[0] != "branch":
        return False
    flags = set(args[1:])
    if "-D" in flags:
        return True
    return bool(flags & {"-d", "--delete"}) and bool(flags & {"-f", "--force"})


CUSTOM_FNS: dict[str, Callable[[list[str]], bool]] = {
    "curl_mutating_remote": curl_mutating_remote,
    "sed_inline_long": sed_inline_long,
    "rm_recursive": rm_recursive,
    "all_paths_inside_project": all_paths_inside_project,
    "all_paths_under_scratch": all_paths_under_scratch,
    "git_config_read": git_config_read,
    "git_push_to_protected_branch": git_push_to_protected_branch,
    "git_branch_force_delete": git_branch_force_delete,
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
        logger.info('decision=%s command="%s" matched=shell_c_recurse agent=%s', verdict, cmd_str[:200], agent_type)
        return verdict, reason, "shell_c_recurse"
    decision = _classify(cmd_str)
    verdict, _, detail = decision
    logger.info('decision=%s command="%s" matched=%s agent=%s', verdict, cmd_str[:200], detail, agent_type)
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
    sys.stdout.write(
        json.dumps(
            {
                "hookSpecificOutput": {
                    "hookEventName": "PreToolUse",
                    "permissionDecision": decision,
                    "permissionDecisionReason": reason,
                }
            }
        )
        + "\n"
    )


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
    logger.info('decision=deny command="%s" matched=%s agent=%s', command[:120], tag, agent_type)


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
_AST_DETECTORS: list[tuple[Callable[[Iterable[BashNode]], bool], str, str]] = [
    (has_function_def, _FUNCTION_DEF_REASON, "function_def"),
    (python_c_not_after_pipe, _PYTHON_C_REASON, "python_c_standalone"),
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
    """Run ACL on each sub-command and keep the strictest decision (deny > ask > allow)."""
    final: Verdict = ("allow", "")
    for sub_cmd in split_chained_commands(command):
        decision, reason, _ = check_command(sub_cmd, logger, agent_type=agent_type)
        if DECISION_PRIORITY[decision] > DECISION_PRIORITY[final[0]]:
            final = (decision, reason)
    return final


def _decide(command: str, logger: logging.Logger, agent_type: str) -> Verdict:
    for gate in _GATES:
        verdict = gate(command, logger, agent_type)
        if verdict is not None:
            return verdict
    return _resolve_chained(command, logger, agent_type)


def _has_timeout_prefix(command: str) -> bool:
    """True iff the command already runs under a leading `timeout` (so its wait is bounded)."""
    try:
        parts = _strip_wrapper(_strip_env_assignments(shlex.split(command)))
    except ValueError:
        return False
    return bool(parts) and Path(parts[0]).name == "timeout"


def _bound_wait_loop(command: str) -> str | None:
    """If `command` is an unbounded poll loop, return it wrapped in `timeout`; else None.

    Reached only for an otherwise-`allow` command, so bashlex already parsed it cleanly. Covers a
    bare loop and a loop hidden inside `bash -c '…'`; a loop already under `timeout` is left alone.
    """
    if _has_timeout_prefix(command):
        return None
    if wait_loop_unbounded(bashlex.parse(command)):
        return f"timeout {WAIT_TIMEOUT_SECONDS} bash -c {shlex.quote(command)}"
    script = _extract_shell_c(command)
    if script is not None and wait_loop_unbounded(bashlex.parse(script)):
        return f"timeout {WAIT_TIMEOUT_SECONDS} {command}"
    return None


def main() -> None:
    """PreToolUse entry point: read stdin payload, emit allow/ask/deny (or a bounded rewrite)."""
    data = json.loads(sys.stdin.read())
    tool_input = data.get("tool_input", {}) if data.get("tool_name") == "Bash" else {}
    command = tool_input.get("command", "")
    if not command:
        return
    ensure_scratch_dir()
    agent_type = data.get("agent_type") if data.get("agent_id") is not None else "main"
    logger = setup_logging()
    decision, reason = _decide(command, logger, agent_type)
    if decision == "allow":
        wrapped = _bound_wait_loop(command)
        if wrapped is not None:
            logger.info('decision=rewrite command="%s" matched=wait_loop_unbounded agent=%s', command[:120], agent_type)
            _emit_rewrite(tool_input, wrapped)
            return
    _emit(decision, reason)


if __name__ == "__main__":
    main()
