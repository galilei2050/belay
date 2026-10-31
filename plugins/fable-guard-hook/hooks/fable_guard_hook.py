#!/usr/bin/env python3
"""Delegation gate for Claude Code file edits — deny from the main thread, which runs on Fable.

delegation-hook already denies a *subagent* spawn priced at Fable (the most expensive tier on
this machine); this is its mirror for the thread that spawns them. The main thread has no
cheaper `model` to pick — it IS the Fable session — so the only lever is to not edit code there
at all: hand the edit to a subagent (`Agent`, background, a cheaper `model`) or another Claude
session (`SendMessage`).

The PreToolUse payload carries no field naming the session's model (verified against every hook
in this repo and the transcript JSONL schema), so this can't check "is this call running on
Fable" directly. It uses the same proxy delegation-hook already relies on: `agent_id` is present
only inside a subagent, so its absence means the main thread — Fable, on this machine, by
definition — and presence means the delegation this hook exists to force is already happening.

Only `deny` is ever emitted. Every other case emits nothing, so this hook never overrides a
sibling hook's allow/ask on the same call (same discipline as branch-guard-hook).
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

PROJECT_DIR = str(Path(os.environ.get("CLAUDE_PROJECT_DIR") or Path.cwd()).resolve())

# Mirrors branch-guard-hook's SCRATCH_SUBDIR: the sanctioned throwaway zone, exempt here for the
# same reason — one-off scripts shouldn't need a subagent spawned just to write them.
SCRATCH_SUBDIR = ".scratch"

_REASON = (
    "This is the main thread — Fable, the most expensive tier on this machine — and it does not "
    "edit code directly. Delegate this {tool}: spawn a subagent (`Agent`, background, a cheaper "
    "`model`: opus/sonnet/haiku) or hand it to another Claude session (`SendMessage`). "
    "`.scratch/` stays writable here for one-off scripts."
)


def classify(tool_name: str, file_path: str, agent_id: str | None) -> str | None:
    """The deny reason for a file-tool call, or None to stay out of the way.

    Path checks run before the `agent_id` check so an out-of-project or scratch write costs
    nothing extra — those are never this hook's concern, subagent or not.
    """
    project = Path(PROJECT_DIR).resolve()
    real = Path(file_path).resolve()  # collapses `..`, so a traversal can't dodge the checks below
    if project not in real.parents:
        return None  # out of project — fs-acl-hook's boundary, not ours
    if real.relative_to(project).parts[0] == SCRATCH_SUBDIR:
        return None
    if agent_id is not None:
        return None  # a subagent editing is the delegation this hook exists to force
    return _REASON.format(tool=tool_name)


def _emit(reason: str) -> None:
    sys.stdout.write(
        json.dumps(
            {
                "hookSpecificOutput": {
                    "hookEventName": "PreToolUse",
                    "permissionDecision": "deny",
                    "permissionDecisionReason": reason,
                }
            }
        )
        + "\n"
    )


def main() -> None:
    """PreToolUse entry point: read the stdin payload, emit a deny, or nothing."""
    data = json.loads(sys.stdin.read())
    tool_input = data["tool_input"]
    # NotebookEdit is the one tool in the matcher that names its target `notebook_path`.
    file_path = tool_input.get("file_path") or tool_input["notebook_path"]
    agent_id = data.get("agent_id")
    reason = classify(data["tool_name"], file_path, agent_id)
    if reason is not None:
        _emit(reason)


if __name__ == "__main__":
    main()
