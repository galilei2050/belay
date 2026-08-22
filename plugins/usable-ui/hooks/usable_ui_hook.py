#!/usr/bin/env python3
"""PreToolUse hook: the UI rules while a surface is written, the UI reviewers once it lands.

Two moments, one predicate — *is this file a user-facing surface?* Nothing else is
inspected. The hook does not read the markup, does not judge a label, and knows the
reviewers only by name; every piece of judgment lives in `skills/` and `agents/`, which
are prompts rather than code.

Advisory by design. Only `hookSpecificOutput.additionalContext` is emitted and never a
`permissionDecision`, so the call still goes through the normal permission flow and
acl-hook keeps the last word. Claude Code reads `additionalContext` on the *next* model
request, which is why the commit roster points the panel at `HEAD` — by then the commit
has already landed.
"""

from __future__ import annotations

import hashlib
import json
import re
import shutil
import subprocess
import sys
from pathlib import Path, PurePosixPath

STATE_PATH = Path.home() / ".claude" / "usable-ui" / "state.json"
GIT = shutil.which("git")

# How many sessions of "already nudged" to remember. The file is a de-duplicator, not a
# log, and a session id is never revisited once its session ends.
SESSIONS_REMEMBERED = 20

# The panel. Order sets the reading order of the merged report: the reviewers whose
# findings block a user outright come before the ones that merely confuse them.
REVIEWERS = (
    "ui-a11y-reviewer",
    "ui-control-reviewer",
    "ui-state-reviewer",
    "ui-copy-reviewer",
    "ui-layout-reviewer",
)

# Files that are unambiguously a user-facing surface. Deliberately narrow: a false
# positive costs five subagents on a commit, so `.swift`, `.dart`, `.kt` and `.py` stay
# out even though SwiftUI, Flutter and Compose live in them — telling a widget from a
# repository would mean reading the file, which is the reviewers' job, not the hook's.
# Stylesheets are out for the same reason; a style change that matters travels with the
# component it styles, and that component is on this list.
UI_SUFFIXES = (
    ".jsx",
    ".tsx",
    ".vue",
    ".svelte",
    ".astro",
    ".html",
    ".htm",
    ".hbs",
    ".ejs",
    ".pug",
    ".erb",
    ".haml",
    ".liquid",
    ".twig",
    ".njk",
    ".mustache",
    ".jinja",
    ".jinja2",
    ".j2",
    ".blade.php",
    ".razor",
    ".cshtml",
    ".xaml",
    ".axaml",
    ".qml",
)

EDIT_TOOLS = frozenset({"Write", "Edit", "MultiEdit", "NotebookEdit"})

# `git -C dir commit`, `foo && git commit -m x`, `git commit`. Leading `-`/`--` options
# belong to git itself (-C, --no-pager); the subcommand is the first bare word.
_GIT_COMMIT_RE = re.compile(r"(?:^|[;&|(]|&&)\s*git\b(?:\s+-{1,2}\S+(?:\s+\S+)?)*\s+commit\b")
_DRY_RUN_RE = re.compile(r"(?<![\w-])--dry-run(?![\w-])")
# `-a` / `-am` stage tracked files at commit time, so the index is empty until then and
# the scope has to come from the worktree instead. `(?!-)` keeps `--amend` out.
_STAGE_ALL_RE = re.compile(r"(?<![\w-])(?:-(?!-)\w*a\w*|--all)(?![\w-])")

_SKILL_PROMPT = """\
You are editing a user-facing surface (`{path}`). Invoke the `usable-ui:ui-decisions` skill
and work from it — it decides the wording, the control, the placement and the states, and it
carries the accessibility thresholds that are pass/fail rather than preference.

The four questions it exists to stop you guessing at:
- **What class is this element?** Action, destination, object, setting, or event. The class
  picks the grammar (`Send SMS` vs `Outbound SMS` vs `SMS sent`) and the control.
- **Is this the right control?** A `<div onClick>` is never one. A switch takes effect
  immediately; a checkbox waits for Save.
- **Where does it go?** Dialog button order belongs to the host platform; a form's submit
  does not follow it.
- **Which states does this screen owe?** Loading, empty, error, partial, success — and an
  empty state that a failed request also renders is a bug, not a blank.

Shown once per session."""

_PANEL_PROMPT = """\
The commit you just made touches user-facing surfaces:

{files}

Put it through the UI panel. Dispatch all {count} reviewers **in a single message** so they
run in parallel. Each is read-only and reviews the same scope: the diff of the commit that
just landed (`git show HEAD`), plus whatever surrounding files it needs for context.

{roster}

Then:
- Merge their findings. Drop anything a reviewer could not point at a concrete line.
- Expect overlap at the seams and report each defect once — a `<div onClick>` is one finding
  with two reasons, not two findings.
- Fix what survives, and commit the fixes. Do not amend unless the commit is unpushed and
  the fix is trivial.
- If nothing survives, say so in one line and move on.

**Dispatch only over UI the panel has not seen.** A round costs {count} subagents of the
user's money. A commit that only applies the findings from the round you just ran is not new
work: say so in one line and dispatch nobody.

The panel is advisory — none of this blocks the commit that already happened."""


def is_ui_path(path: str) -> bool:
    """True iff this file is a user-facing surface, judged by name alone."""
    return PurePosixPath(path).name.lower().endswith(UI_SUFFIXES)


def edited_path(tool_input: dict[str, object]) -> str:
    """The file an edit tool is about to write. `NotebookEdit` names its target differently."""
    target = tool_input.get("file_path") or tool_input.get("notebook_path") or ""
    return target if isinstance(target, str) else ""


def is_reviewable_commit(command: str) -> bool:
    """True iff this Bash command actually creates a commit (so `--dry-run` is out)."""
    return bool(_GIT_COMMIT_RE.search(command)) and not _DRY_RUN_RE.search(command)


def _git(cwd: str, *args: str) -> str | None:
    """Run a read-only git command, or None if git is absent or refuses (not a repo, bad rev)."""
    if GIT is None:
        return None
    # S603: every `args` at every call site is a string literal, the binary is resolved by
    # `shutil.which`, and there is no shell — the only caller-supplied value is `cwd`.
    result = subprocess.run((GIT, *args), cwd=cwd, capture_output=True, text=True, check=False)  # noqa: S603
    return result.stdout if result.returncode == 0 else None


def committed_ui_files(cwd: str, command: str) -> list[str]:
    """The user-facing files about to be committed, in the order git reports them."""
    scope = "HEAD" if _STAGE_ALL_RE.search(command) else "--cached"
    names = _git(cwd, "diff", scope, "--name-only")
    if names is None:
        return []
    return [line for line in names.splitlines() if line and is_ui_path(line)]


def review_digest(cwd: str, command: str) -> str | None:
    """Digest of the UI about to be committed, or None if there is none.

    This is what makes the roster idempotent: a commit rejected by pre-commit and retried
    carries the same content, and re-dispatching five subagents over it is pure waste.
    Once the agent changes something the digest moves and the panel runs again.
    """
    files = committed_ui_files(cwd, command)
    if not files:
        return None
    scope = "HEAD" if _STAGE_ALL_RE.search(command) else "--cached"
    diff = _git(cwd, "diff", scope, "--", *files)
    if not diff or not diff.strip():
        return None
    return hashlib.sha256(diff.encode()).hexdigest()


def _read_state() -> dict[str, object]:
    """The de-duplication state, or an empty one if it does not exist yet."""
    if not STATE_PATH.exists():
        return {}
    state = json.loads(STATE_PATH.read_text())
    return state if isinstance(state, dict) else {}


def _write_state(state: dict[str, object]) -> None:
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    STATE_PATH.write_text(json.dumps(state))


def claim_session_nudge(session_id: str) -> bool:
    """True the first time this session edits a UI file, False every time after.

    The rules are worth one interruption per session; repeating them on every component
    file trains the agent to skim past them.
    """
    state = _read_state()
    seen = state.get("nudged_sessions")
    sessions: list[str] = [s for s in seen if isinstance(s, str)] if isinstance(seen, list) else []
    if session_id in sessions:
        return False
    state["nudged_sessions"] = [*sessions, session_id][-SESSIONS_REMEMBERED:]
    _write_state(state)
    return True


def claim_review(repo: str, digest: str) -> bool:
    """True unless the panel was already dispatched over this exact UI content in this repo."""
    state = _read_state()
    reviewed = state.get("reviewed")
    digests: dict[str, object] = reviewed if isinstance(reviewed, dict) else {}
    if digests.get(repo) == digest:
        return False
    digests[repo] = digest
    state["reviewed"] = digests
    _write_state(state)
    return True


def emit(context: str) -> None:
    """Hand the agent some context and nothing else — no permission decision, ever."""
    payload = {"hookSpecificOutput": {"hookEventName": "PreToolUse", "additionalContext": context}}
    sys.stdout.write(json.dumps(payload) + "\n")


def on_edit(data: dict[str, object]) -> None:
    """An edit tool is about to touch a file: nudge once per session if it is a UI file."""
    tool_input = data.get("tool_input")
    path = edited_path(tool_input) if isinstance(tool_input, dict) else ""
    if not path or not is_ui_path(path):
        return
    session_id = data.get("session_id")
    if not claim_session_nudge(session_id if isinstance(session_id, str) else ""):
        return
    emit(_SKILL_PROMPT.format(path=path))


def on_bash(data: dict[str, object]) -> None:
    """A Bash call is about to run: hand over the roster if it commits UI."""
    tool_input = data.get("tool_input")
    command = tool_input.get("command", "") if isinstance(tool_input, dict) else ""
    if not isinstance(command, str) or not is_reviewable_commit(command):
        return

    cwd = data.get("cwd")
    cwd = cwd if isinstance(cwd, str) and cwd else "."
    toplevel = _git(cwd, "rev-parse", "--show-toplevel")
    if toplevel is None:
        return  # not a git repo — the commit will fail on its own
    digest = review_digest(cwd, command)
    if digest is None:
        return  # nothing user-facing in this commit
    if not claim_review(toplevel.strip(), digest):
        return

    files = "\n".join(f"- `{name}`" for name in committed_ui_files(cwd, command))
    roster = "\n".join(f"- `usable-ui:{name}`" for name in REVIEWERS)
    emit(_PANEL_PROMPT.format(files=files, count=len(REVIEWERS), roster=roster))


def main() -> None:
    """PreToolUse entry point: emit context for one of the two moments, or nothing at all."""
    data = json.loads(sys.stdin.read())
    tool_name = data.get("tool_name")
    if tool_name in EDIT_TOOLS:
        on_edit(data)
    elif tool_name == "Bash":
        on_bash(data)


if __name__ == "__main__":
    main()
