#!/usr/bin/env python3
"""Branch-state gate for Claude Code Write / Edit — deny edits while trunk is checked out.

The branch-aware sibling of fs-acl-hook (which decides by path alone). acl-hook already refuses
`git push` to main/master, but that fires at the end, once a pile of commits already sits on
trunk and untangling them costs a branch-and-cherry-pick. This one fires on the first edit, when
`git checkout -b` still fixes everything by carrying the uncommitted change across.

Only `deny` is ever emitted. Every other case emits nothing, so this hook never overrides a
sibling hook's allow/ask on the same call.

Exempt while on trunk:
  - `.scratch/` — throwaways, never committed (acl-hook gitignores it)
  - `.claude/`  — harness config and session state, routinely tended on trunk
  - anything outside the project — fs-acl-hook already owns that boundary
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

PROJECT_DIR = str(Path(os.environ.get("CLAUDE_PROJECT_DIR") or Path.cwd()).resolve())

PROTECTED_BRANCHES = frozenset({"main", "master"})

# Kept in sync with fs-acl-hook's SCRATCH_SUBDIR and its `~/.claude` carve-out: the two dirs whose
# whole point is to be written without ceremony.
EXEMPT_SUBDIRS = (".scratch", ".claude")

WRITE_TOOLS = frozenset({"Write", "Edit", "MultiEdit", "NotebookEdit"})

_HEAD_REF_PREFIX = "ref: refs/heads/"

_REASON = (
    "`{branch}` is trunk — it moves only through a reviewed PR merge, so nothing should be edited "
    "while it is checked out. Branch first: `git checkout -b <feature>` carries every uncommitted "
    "change with you, then redo this {tool}. `.scratch/` and `.claude/` stay writable on trunk."
)

Decision = tuple[str, str]


def _git_dir(start: Path) -> Path | None:
    """The git dir governing `start`, or None when it is not in a checkout.

    Walks up to the nearest `.git`: a directory in a normal checkout, a file holding
    `gitdir: <path>` in a linked worktree or submodule. Following the invocation's cwd rather than
    PROJECT_DIR is what makes this correct inside a worktree, where CLAUDE_PROJECT_DIR still names
    the main checkout and would answer with trunk's HEAD instead of the worktree's own.
    """
    git = next((p / ".git" for p in (start, *start.parents) if (p / ".git").exists()), None)
    if git is None:
        return None
    if git.is_file():
        # `gitdir:` is relative for submodules and for worktrees made with --relative-paths, and is
        # relative to the dir holding the `.git` file — `/` leaves an absolute path unchanged.
        pointer = git.read_text(encoding="utf-8").strip().removeprefix("gitdir:").strip()
        return git.parent / pointer
    return git


def current_branch(cwd: str) -> str | None:
    """Name of the checked-out branch, or None outside a checkout / on a detached HEAD."""
    git = _git_dir(Path(cwd).resolve())
    if git is None:
        return None
    head = git / "HEAD"
    if not head.is_file():
        return None
    ref = head.read_text(encoding="utf-8").strip()
    if not ref.startswith(_HEAD_REF_PREFIX):
        return None  # detached HEAD names no branch, so no branch policy applies
    return ref.removeprefix(_HEAD_REF_PREFIX)


def classify(tool_name: str, file_path: str, branch: str | None) -> Decision | None:
    """Decide (decision, reason) for a file-tool call, or None to stay out of the way."""
    if tool_name not in WRITE_TOOLS or branch not in PROTECTED_BRANCHES:
        return None
    project = Path(PROJECT_DIR).resolve()
    real = Path(file_path).resolve()  # collapses `..`, so a traversal can't dodge the check
    if project not in real.parents:
        return None  # out of project — fs-acl-hook's boundary, not ours
    if real.relative_to(project).parts[0] in EXEMPT_SUBDIRS:
        return None
    return ("deny", _REASON.format(branch=branch, tool=tool_name))


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
    """PreToolUse entry point: read the stdin payload, emit a deny, or nothing."""
    data = json.loads(sys.stdin.read())
    tool_input = data.get("tool_input", {})
    file_path = tool_input.get("file_path") or tool_input.get("notebook_path")
    if not file_path:
        return
    result = classify(data.get("tool_name", ""), file_path, current_branch(data.get("cwd") or PROJECT_DIR))
    if result is None:
        return
    _emit(*result)


if __name__ == "__main__":
    main()
