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

# `git -C dir commit`, `foo && git commit -m x`. Leading `-`/`--` options belong to git itself
# (-C, --no-pager); the subcommand is the first bare word. Same shape as review-panel's.
_GIT_SUBCOMMAND = r"(?:^|[;&|(]|&&)\s*git\b(?:\s+-{{1,2}}\S+(?:\s+\S+)?)*\s+{sub}\b"
_GIT_COMMIT_RE = re.compile(_GIT_SUBCOMMAND.format(sub="commit"))
_GIT_PUSH_RE = re.compile(_GIT_SUBCOMMAND.format(sub="push"))
_DRY_RUN_RE = re.compile(r"(?<![\w-])--dry-run(?![\w-])")


def nudge_for(data: dict[str, object]) -> str | None:
    """The text to put in front of the agent, or None if this call is none of our business."""
    tool_input = data.get("tool_input")
    if data.get("tool_name") != "Bash" or not isinstance(tool_input, dict):
        return None
    command = str(tool_input.get("command", ""))
    if _DRY_RUN_RE.search(command):
        return None
    is_push = bool(_GIT_PUSH_RE.search(command))
    if not is_push and not _GIT_COMMIT_RE.search(command):
        return None

    step = next_step(str(data.get("cwd") or "."), after_push=is_push)
    return step.nudge if step else None


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
