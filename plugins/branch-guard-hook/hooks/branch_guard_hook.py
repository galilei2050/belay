#!/usr/bin/env python3
"""Branch-state gate for Claude Code file edits — deny while trunk is checked out.

The branch-aware sibling of fs-acl-hook (which decides by path alone). acl-hook already refuses
`git push` to main/master, but that fires at the end, once a pile of commits already sits on
trunk and untangling them costs a branch plus a cherry-pick. This one fires on the first edit,
where `git checkout -b` still fixes everything by carrying the uncommitted change across.

Only `deny` is ever emitted. Every other case emits nothing, so this hook never overrides a
sibling hook's allow/ask on the same call.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

PROJECT_DIR = str(Path(os.environ.get("CLAUDE_PROJECT_DIR") or Path.cwd()).resolve())

PROTECTED_BRANCHES = frozenset({"main", "master"})

# Mirrors fs-acl-hook's SCRATCH_SUBDIR: the sanctioned throwaway zone, and the only in-project
# path that never reaches a PR. `<project>/.claude` is deliberately NOT exempt — it holds tracked
# source (skills) and whole worktree checkouts, and fs-acl-hook likewise treats a project's own
# `.claude` as ordinary project config rather than the agent's home.
SCRATCH_SUBDIR = ".scratch"

_HEAD_REF_PREFIX = "ref: refs/heads/"

_REASON = (
    "`{branch}` is trunk — it moves only through a reviewed PR merge, so nothing should be edited "
    "while it is checked out. Branch first: `git checkout -b <feature>` carries every uncommitted "
    "change with you, then redo this {tool}. `.scratch/` stays writable on trunk."
)


def _git_dir(start: Path) -> Path | None:
    """The git dir governing `start`, or None when it is not in a checkout.

    Vendored from acl-hook's `_git_dir` — plugins here are self-contained by policy
    (docs/AUTHORING.md), so the walk lives in both. Nearest `.git` wins: a directory in a normal
    checkout, a file holding `gitdir: <path>` in a linked worktree or submodule.
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


def current_branch(path: Path) -> str | None:
    """Name of the branch checked out in `path`'s own checkout, or None outside one / detached.

    Resolved from the file under judgement, never from the session's cwd: HEAD is per-checkout
    state, and a session that entered a worktree still reaches back into the main checkout. Asking
    the worktree's HEAD about a main-checkout file clears an edit to trunk; asking the main
    checkout about a worktree file denies one that is already safely on a feature branch.

    A git dir with no readable HEAD raises rather than returning None — for a hook whose only job
    is to say "no", "I can't tell" has to be loud instead of silently disabling the guard.
    """
    git = _git_dir(path)
    if git is None:
        return None  # not a checkout at all, so no branch policy applies
    ref = (git / "HEAD").read_text(encoding="utf-8").strip()
    if not ref.startswith(_HEAD_REF_PREFIX):
        return None  # detached HEAD names no branch
    return ref.removeprefix(_HEAD_REF_PREFIX)


def classify(tool_name: str, file_path: str) -> str | None:
    """The deny reason for a file-tool call, or None to stay out of the way.

    Path checks run before the branch lookup so the common in-project edit costs no filesystem
    walk when it is already excluded.
    """
    project = Path(PROJECT_DIR).resolve()
    real = Path(file_path).resolve()  # collapses `..`, so a traversal can't dodge the checks below
    if project not in real.parents:
        return None  # out of project — fs-acl-hook's boundary, not ours
    if real.relative_to(project).parts[0] == SCRATCH_SUBDIR:
        return None
    branch = current_branch(real.parent)
    if branch not in PROTECTED_BRANCHES:
        return None
    return _REASON.format(branch=branch, tool=tool_name)


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
    reason = classify(data["tool_name"], file_path)
    if reason is not None:
        _emit(reason)


if __name__ == "__main__":
    main()
