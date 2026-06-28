#!/usr/bin/env python3
"""File-tool ACL hook for Claude Code Write / Edit / Read.

The file-path sibling of acl-hook (which gates Bash by command). Single job: for every
file-tool call, decide allow / ask / deny by the *path* it touches, so the agent writes
throwaways to the sanctioned scratch dir, never scatters files in /tmp or another repo,
and never pokes `.git/` directly.

Decisions (first match wins):
  - any path inside `.git/`            → deny  (read AND write; use `git` commands)
  - write under `.scratch/`            → allow (suppress prompt; the scratch zone)
  - write outside the project          → deny  (→ `.scratch/`, or cd into that repo)
  - read outside the project           → ask   (confirm a one-off cross-repo read)
  - anything else (in-project)         → defer to the normal permission flow (emit nothing)

Emits the PreToolUse `hookSpecificOutput` JSON (not exit codes) because `allow` must
actively suppress the prompt and `ask` must escalate — neither is expressible via exit 0/2.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

PROJECT_DIR = str(Path(os.environ.get("CLAUDE_PROJECT_DIR") or Path.cwd()).resolve())

# Must match acl-hook's SCRATCH_SUBDIR — the one dir where writes (and `rm`) are free.
SCRATCH_SUBDIR = ".scratch"

WRITE_TOOLS = {"Write", "Edit", "MultiEdit", "NotebookEdit"}
READ_TOOLS = {"Read"}

_GIT_REASON = (
    "`.git/` is off-limits — no reads or writes. It's git's internal state; inspect it with "
    "`git` commands (status/log/show/diff), not file reads. For a commit message, Write "
    "`.scratch/COMMIT_MSG` then `git commit -F .scratch/COMMIT_MSG` (the `.scratch/` dir is "
    "auto-created and gitignored; files there need no Write prompt and `rm` is allowed)."
)
_OUT_OF_PROJECT_WRITE_REASON = (
    "Writing `{path}` is outside the current project (`{project}`). Don't scatter temp files in "
    "/tmp or edit across repos with `../`. Throwaways go in `.scratch/` (in-tree, gitignored, no "
    "prompt). To work on another project, cd into it / open it as its own session so its files "
    "are in scope."
)
_OUT_OF_PROJECT_READ_REASON = (
    "Reading `{path}` is outside the current project (`{project}`). If you mean to work in that "
    "repo, cd into it / open it as its own session. Otherwise confirm this one-off cross-repo "
    "read, or ask the user to connect the directory (permissions.additionalDirectories)."
)

Decision = tuple[str, str]


def _under(root: Path, real: Path) -> bool:
    """True iff `real` is `root` itself or nested inside it."""
    return real == root or root in real.parents


def _classify_write(project: Path, real: Path, file_path: str) -> Decision | None:
    if _under((project / SCRATCH_SUBDIR).resolve(), real):
        return ("allow", "")  # the scratch zone — suppress the prompt
    if not _under(project, real):
        return ("deny", _OUT_OF_PROJECT_WRITE_REASON.format(path=file_path, project=PROJECT_DIR))
    return None  # in-project source write → normal flow (acceptEdits / review)


def _classify_read(project: Path, real: Path, file_path: str) -> Decision | None:
    if not _under(project, real):
        return ("ask", _OUT_OF_PROJECT_READ_REASON.format(path=file_path, project=PROJECT_DIR))
    return None  # in-project read → normal flow


def classify(tool_name: str, file_path: str) -> Decision | None:
    """Decide (decision, reason) for a file-tool call, or None to defer to the normal flow."""
    project = Path(PROJECT_DIR).resolve()
    real = Path(file_path).resolve()  # collapses `..`, so a traversal can't dodge the boundary
    if _under((project / ".git").resolve(), real):
        return ("deny", _GIT_REASON)  # off-limits to both read and write
    if tool_name in WRITE_TOOLS:
        return _classify_write(project, real, file_path)
    return _classify_read(project, real, file_path)


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


def main() -> None:
    """PreToolUse entry point: read stdin payload, emit allow/ask/deny, or nothing (defer)."""
    data = json.loads(sys.stdin.read())
    tool_name = data.get("tool_name", "")
    if tool_name not in WRITE_TOOLS | READ_TOOLS:
        return
    tool_input = data.get("tool_input", {})
    file_path = tool_input.get("file_path") or tool_input.get("notebook_path")
    if not file_path:
        return
    result = classify(tool_name, file_path)
    if result is None:
        return
    _emit(*result)


if __name__ == "__main__":
    main()
