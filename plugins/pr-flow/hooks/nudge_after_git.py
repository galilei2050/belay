#!/usr/bin/env python3
"""PostToolUse hook: after `git commit` / `git push`, say what the branch still owes.

Fires at the one moment the branch is already what the agent is thinking about — the commit
just landed, the push just went out — and states the next step then, instead of hoping the
agent remembers it four tool calls later. Advisory only: it emits `additionalContext` and no
decision, so it can never fail a git command.

`require_pr.py` is the backstop for when this is ignored.
"""

from __future__ import annotations

import json
import re
import sys

from branch_state import next_step
from nudges import NUDGES

# A Bash call is a list of segments; each is judged on its own, so `git commit && git push
# --dry-run` is a real commit next to a dry run rather than one ambiguous string.
_SEGMENT_SPLIT_RE = re.compile(r"[;&|\n()]+")
# Quoted spans are blanked before anything is read out of a segment: `--dry-run` inside a commit
# message is prose, not a flag.
_QUOTED_RE = re.compile(r"'[^']*'|\"[^\"]*\"")
# `-C dir` / `--git-dir` / `--work-tree` point git at a repository that is not the session's, and
# the branch state we would report is this one's. Stay out of it.
_OTHER_REPO_RE = re.compile(r"(?<![\w-])(?:-C|--git-dir|--work-tree)(?![\w-])")
_DRY_RUN_RE = re.compile(r"(?<![\w-])--dry-run(?![\w-])")
# Leading `-`/`--` options belong to git itself (--no-pager); the subcommand is the first bare word.
_SUBCOMMAND_RES = {sub: re.compile(rf"^\s*git\b(?:\s+-{{1,2}}\S+(?:\s+\S+)?)*\s+{sub}\b") for sub in ("commit", "push")}


def git_subcommands(command: str) -> set[str]:
    """Which of `commit` / `push` this Bash call really runs against the session's own repo."""
    blanked = _QUOTED_RE.sub(lambda match: " " * len(match.group()), command)
    found: set[str] = set()
    for segment in _SEGMENT_SPLIT_RE.split(blanked):
        if _OTHER_REPO_RE.search(segment) or _DRY_RUN_RE.search(segment):
            continue
        found.update(sub for sub, pattern in _SUBCOMMAND_RES.items() if pattern.search(segment))
    return found


def nudge_for(data: dict[str, object]) -> str | None:
    """The text to put in front of the agent, or None if this call is none of our business."""
    tool_input = data.get("tool_input")
    if data.get("tool_name") != "Bash" or not isinstance(tool_input, dict):
        return None
    subcommands = git_subcommands(str(tool_input.get("command", "")))
    if not subcommands:
        return None

    step = next_step(str(data.get("cwd") or "."))
    if step is None:
        return None
    if step.kind == "update" and "push" not in subcommands:
        return None  # only a push can have made the PR body stale
    return NUDGES[step.kind].format(**step._asdict())


def main() -> None:
    """PostToolUse entry point: emit one nudge, or nothing at all."""
    data = json.loads(sys.stdin.read())
    if data.get("hook_event_name") != "PostToolUse":
        return
    nudge = nudge_for(data)
    if nudge is None:
        return
    output = {"hookSpecificOutput": {"hookEventName": "PostToolUse", "additionalContext": nudge}}
    sys.stdout.write(json.dumps(output) + "\n")


if __name__ == "__main__":
    main()
