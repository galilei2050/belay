#!/usr/bin/env python3
"""File-tool ACL hook for Claude Code Write / Edit / Read.

The file-path sibling of acl-hook (which gates Bash by command). Single job: for every
file-tool call, decide allow / ask / deny by the *path* it touches, so the agent writes
throwaways to the sanctioned scratch dir, never scatters files in /tmp or another repo,
and never pokes `.git/` directly.

Decisions (first match wins):
  - any path inside `.git/`            → deny  (read AND write; use `git` commands)
  - credentials under `~/.claude`      → deny  (credential and env files, read AND write)
  - write to a guard file under `~/.claude` → ask (settings/ACL — the agent's own leash)
  - anything else under `~/.claude`    → allow (memory, logs, plans — the agent's own home)
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

# The agent's own home: memory, logs, plans, job scratch. Writable, because that is where
# the harness asks the agent to keep things — a blanket out-of-project deny made the memory
# directory unreachable.
CLAUDE_HOME = (Path.home() / ".claude").resolve()

# Two carve-outs inside it, matched by basename anywhere under CLAUDE_HOME.
# Credentials: never read, never written — nothing the agent does needs their contents.
CREDENTIAL_NAMES = frozenset({".credentials.json", ".env"})
# Guards: the files that decide what the agent may do. Reading them is routine (the
# update-config skill does), but a silent write would let the agent widen its own leash.
GUARD_NAMES = frozenset({"settings.json", "settings.local.json", "acl.json"})

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
_CLAUDE_CREDENTIALS_REASON = (
    "`{path}` holds credentials — off-limits to both read and write. Nothing you are asked to do "
    "needs their contents; if a tool needs auth, run its own login command and let it manage the "
    "file. To store something of your own under `~/.claude`, use the memory directory instead."
)
_CLAUDE_GUARD_REASON = (
    "`{path}` is what decides your own permissions — editing it silently would widen your leash. "
    "Reading it is fine; a write needs the user to see it. Confirm this change, and say in one "
    "line which key you're changing and why."
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


def _classify_claude_home(tool_name: str, real: Path, file_path: str) -> Decision:
    """Decide for a path inside `~/.claude`: deny credentials, ask on guards, allow the rest."""
    if real.name in CREDENTIAL_NAMES:
        return ("deny", _CLAUDE_CREDENTIALS_REASON.format(path=file_path))
    if tool_name in WRITE_TOOLS and real.name in GUARD_NAMES:
        return ("ask", _CLAUDE_GUARD_REASON.format(path=file_path))
    return ("allow", "")


def classify(tool_name: str, file_path: str) -> Decision | None:
    """Decide (decision, reason) for a file-tool call, or None to defer to the normal flow."""
    project = Path(PROJECT_DIR).resolve()
    real = Path(file_path).resolve()  # collapses `..`, so a traversal can't dodge the boundary
    if _under((project / ".git").resolve(), real):
        return ("deny", _GIT_REASON)  # off-limits to both read and write
    if _under(CLAUDE_HOME, real):
        return _classify_claude_home(tool_name, real, file_path)
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
