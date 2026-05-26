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
_PROJECT_ACL_RELPATH = Path(".claude") / "acl.json"
_ACL_CACHE: dict[str, Entry] | None = None


def project_acl_path() -> Path:
    """Where the per-project ACL config lives (auto-installed from bundled default)."""
    return Path(PROJECT_DIR) / _PROJECT_ACL_RELPATH


def _load_acl() -> dict[str, Entry]:
    """Read the project ACL, copying the bundled default on first access."""
    global _ACL_CACHE  # noqa: PLW0603 — module-level cache for the parsed config
    if _ACL_CACHE is not None:
        return _ACL_CACHE
    target = project_acl_path()
    if not target.exists():
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(_BUNDLED_ACL_PATH.read_text(encoding="utf-8"), encoding="utf-8")
    loaded: dict[str, Entry] = json.loads(target.read_text(encoding="utf-8"))
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


# Standalone `python -c "…"` is denied at top level in main() — see python_c_not_after_pipe.
MAX_BASH_LEN = 1500
MAX_BASH_LINES = 10
SED_INLINE_EXPR_MAX = 300


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


CUSTOM_FNS: dict[str, Callable[[list[str]], bool]] = {
    "curl_mutating_remote": curl_mutating_remote,
    "sed_inline_long": sed_inline_long,
    "rm_recursive": rm_recursive,
    "all_paths_inside_project": all_paths_inside_project,
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


def python_c_not_after_pipe(trees: Iterable[BashNode]) -> bool:
    """True iff any `python[3] -c …` invocation is NOT positioned as a pipe receiver."""
    for tree in trees:
        for node, parent, position in _walk_with_parent(tree):
            words = _command_words(node)
            if not words:
                continue
            if Path(words[0]).name not in ("python", "python3"):
                continue
            if "-c" not in words[1:]:
                continue
            if parent is not None and getattr(parent, "kind", None) == "pipeline" and (position or 0) > 0:
                continue
            return True
    return False


def until_loop_with_sleep(trees: Iterable[BashNode]) -> bool:
    """True iff a Bash invocation contains both `until` (reserved word) and a `sleep` command."""
    for tree in trees:
        has_until = False
        has_sleep = False
        for node, _parent, _pos in _walk_with_parent(tree):
            if getattr(node, "kind", None) == "reservedword" and getattr(node, "word", "") == "until":
                has_until = True
            words = _command_words(node)
            if words and Path(words[0]).name == "sleep":
                has_sleep = True
            if has_until and has_sleep:
                return True
    return False


def chained_sleep(trees: Iterable[BashNode]) -> bool:
    """True iff `sleep N` is chained with another command at the same nesting level."""
    for tree in trees:
        for node, parent, _pos in _walk_with_parent(tree):
            words = _command_words(node)
            if not words or Path(words[0]).name != "sleep":
                continue
            if parent is None:
                continue
            siblings = [c for c in _node_children(parent) if c is not node and getattr(c, "kind", None) == "command"]
            if siblings:
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
    '`python -c` is allowed only as a pipe filter (`<cmd> | python3 -c "…"`). Standalone or '
    "`$(python -c …)` is a script masquerading as a command. Options: (1) pipe data in; "
    "(2) Write the script to a file and run it; (3) split into simple Bash builtins or `jq`."
)
_UNTIL_LOOP_REASON = (
    "`until <cond>; do … sleep N … ; done` inline polling is denied — the loop blocks the "
    "agent for the whole wait, can't be interrupted cleanly. Use instead: "
    "`Bash(..., run_in_background=true)` + `Monitor`/`BashOutput`; or "
    "`ScheduleWakeup(delaySeconds=…)` in a /loop session; or `/schedule` (CronCreate) for "
    "recurring runs."
)
_CHAINED_SLEEP_REASON = (
    "`sleep N` chained with another command is denied. Use one of: "
    "(1) `Bash(..., run_in_background=true)` + `Monitor`/`BashOutput`; "
    "(2) `ScheduleWakeup` in a /loop session; "
    "(3) `/schedule` for a cron remote agent."
)

_AST_DETECTORS: list[tuple[Callable[[Iterable[BashNode]], bool], str, str]] = [
    (has_function_def, _FUNCTION_DEF_REASON, "function_def"),
    (python_c_not_after_pipe, _PYTHON_C_REASON, "python_c_standalone"),
    (until_loop_with_sleep, _UNTIL_LOOP_REASON, "until_loop_with_sleep"),
    (chained_sleep, _CHAINED_SLEEP_REASON, "chained_sleep"),
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


def main() -> None:
    """PreToolUse entry point: read stdin payload, emit allow/ask/deny decision."""
    data = json.loads(sys.stdin.read())
    command = data.get("tool_input", {}).get("command", "") if data.get("tool_name") == "Bash" else ""
    if not command:
        return
    agent_type = data.get("agent_type") if data.get("agent_id") is not None else "main"
    _emit(*_decide(command, setup_logging(), agent_type))


if __name__ == "__main__":
    main()
