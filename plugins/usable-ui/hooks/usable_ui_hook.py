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
from pathlib import Path
from typing import TypedDict

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

EDIT_TOOLS = frozenset({"Write", "Edit", "MultiEdit"})

# A quoted span is data, never a flag: `git commit -m "fix the -a flag"` carries no `-a`,
# and `echo 'git commit'` is not a commit. Blanking quotes before anything else is read
# is what keeps a commit message from being parsed as part of the command.
_QUOTED_RE = re.compile(r"'[^']*'|\"[^\"]*\"")
# `git -C dir commit`, `foo && git commit -m x`, `git commit`. Leading `-`/`--` options
# belong to git itself (-C, --no-pager); the subcommand is the first bare word. A newline
# separates two commands exactly like `;` does — without it in the class, `git add -A` on one
# line and `git commit` on the next was invisible here.
_GIT_COMMIT_RE = re.compile(r"(?:^|[;&|(\n]|&&)\s*git\b(?:\s+-{1,2}\S+(?:\s+\S+)?)*\s+commit\b")
# Where the commit's own command ends. Flags after this belong to the next command, and
# `git commit -m x && git push --dry-run` is a real commit beside a dry run, not a dry run.
_SEGMENT_END_RE = re.compile(r"[;&|\n)]")
_DRY_RUN_RE = re.compile(r"(?<![\w-])--dry-run(?![\w-])")
# `-a` / `-am` stage tracked files at commit time, so the index is empty until then and
# the scope has to come from the worktree instead. `(?!-)` keeps `--amend` out.
_STAGE_ALL_RE = re.compile(r"(?<![\w-])(?:-(?!-)\w*a\w*|--all)(?![\w-])")
# `git -C <dir>` commits into a repo the payload's `cwd` does not name, and the panel
# would be pointed at the wrong `HEAD`. A scope the hook cannot resolve is one it stays
# out of.
_OTHER_REPO_RE = re.compile(r"(?<![\w-])(?:-C|--git-dir|--work-tree)(?![\w-])")

_SKILL_PROMPT = """\
You are about to edit a user-facing surface (`{path}`). Invoke the `usable-ui:ui-decisions`
skill and work from it before you write — it decides the wording, the control, the placement
and the states this screen owes, and it carries the accessibility thresholds that are
pass/fail rather than preference.

Shown once per session."""

_PANEL_PROMPT = """\
The commit you just made touches user-facing surfaces:

{files}

Put it through the UI panel. Dispatch all {count} reviewers **in a single message**, each with
`run_in_background: false`, so they run in parallel and hand their reports straight back.
Each is read-only and reviews the same scope: the diff of the commit that
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
    return path.lower().endswith(UI_SUFFIXES)


def commit_flags(command: str) -> str | None:
    """The flags git itself will see on a real commit, or None if this is not one.

    Quoted spans are blanked and everything before `git … commit` is dropped, so neither a
    message mentioning `--dry-run` nor an unrelated `ls -la` earlier in the line can be read
    as a flag on the commit.
    """
    unquoted = _QUOTED_RE.sub(" ", command)
    match = _GIT_COMMIT_RE.search(unquoted)
    if match is None:
        return None
    end = _SEGMENT_END_RE.search(unquoted, match.end())
    flags = unquoted[match.start() : end.start() if end else len(unquoted)]
    return None if _DRY_RUN_RE.search(flags) else flags


def _git(cwd: str, *args: str) -> str | None:
    """Run a read-only git command, or None if git is absent or refuses (not a repo, bad rev)."""
    if GIT is None:
        return None
    # S603: no shell and the binary is resolved by `shutil.which`; the only values not
    # written here are `cwd` and the paths git itself reported, which are passed after `--`.
    result = subprocess.run((GIT, *args), cwd=cwd, capture_output=True, text=True, check=False)  # noqa: S603
    return result.stdout if result.returncode == 0 else None


class State(TypedDict, total=False):
    """What the hook remembers between runs: sessions already nudged, UI already reviewed.

    `total=False` because a fresh state is `{}` — the file is written the first time either
    half claims something, and each half only ever reads its own key.
    """

    nudged_sessions: list[str]
    reviewed: dict[str, str]


def _read_state() -> State:
    """The de-duplication state, or an empty one if it does not exist yet."""
    return json.loads(STATE_PATH.read_text()) if STATE_PATH.exists() else State()


def _write_state(state: State) -> None:
    """Replace the state file atomically.

    Claude Code issues parallel tool calls, so two copies of this hook can run at once. The
    rename keeps a concurrent reader from landing on a half-written file; a genuinely
    simultaneous pair can still each claim the same nudge, which costs one repeated message.
    """
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    pending = STATE_PATH.with_suffix(".tmp")
    pending.write_text(json.dumps(state))
    pending.replace(STATE_PATH)


def claim_session_nudge(session_id: str) -> bool:
    """True the first time this session edits a UI file, False every time after.

    The rules are worth one interruption per session; repeating them on every component
    file trains the agent to skim past them.
    """
    state = _read_state()
    sessions: list[str] = state.get("nudged_sessions", [])
    if session_id in sessions:
        return False
    state["nudged_sessions"] = [*sessions, session_id][-SESSIONS_REMEMBERED:]
    _write_state(state)
    return True


def claim_review(repo: str, digest: str) -> bool:
    """True unless the panel was already dispatched over this exact UI content in this repo.

    A commit rejected by `pre-commit` and retried carries the same UI, and re-dispatching
    five subagents over it is pure waste. Once the agent changes something the digest moves
    and the panel runs again.
    """
    state = _read_state()
    reviewed: dict[str, str] = state.setdefault("reviewed", {})
    if reviewed.get(repo) == digest:
        return False
    reviewed[repo] = digest
    _write_state(state)
    return True


def emit(context: str) -> None:
    """Hand the agent some context and nothing else — no permission decision, ever."""
    payload = {"hookSpecificOutput": {"hookEventName": "PreToolUse", "additionalContext": context}}
    sys.stdout.write(json.dumps(payload) + "\n")


def on_edit(path: str, session_id: str) -> None:
    """An edit tool is about to touch a file: nudge once per session if it is a UI file."""
    if not is_ui_path(path):
        return
    if not claim_session_nudge(session_id):
        return
    emit(_SKILL_PROMPT.format(path=path))


def on_bash(command: str, cwd: str) -> None:
    """A Bash call is about to run: hand over the roster if it commits UI."""
    flags = commit_flags(command)
    if flags is None or _OTHER_REPO_RE.search(flags):
        return

    toplevel = _git(cwd, "rev-parse", "--show-toplevel")
    if toplevel is None:
        return  # not a git repo — the commit will fail on its own

    scope = "HEAD" if _STAGE_ALL_RE.search(flags) else "--cached"
    # `-z` because `--name-only` alone C-quotes any path outside ASCII, and a quoted name
    # matches no suffix — every non-Latin filename would drop out of the panel's sight.
    names = _git(cwd, "diff", scope, "--name-only", "-z")
    files = [name for name in names.split("\0") if name and is_ui_path(name)] if names else []
    if not files:
        return  # nothing user-facing in this commit

    diff = _git(cwd, "diff", scope, "--", *files)
    if not diff:
        return
    if not claim_review(toplevel.strip(), hashlib.sha256(diff.encode()).hexdigest()):
        return

    listing = "\n".join(f"- `{name}`" for name in files)
    roster = "\n".join(f"- `usable-ui:{name}`" for name in REVIEWERS)
    emit(_PANEL_PROMPT.format(files=listing, count=len(REVIEWERS), roster=roster))


def main() -> None:
    """PreToolUse entry point: emit context for one of the two moments, or nothing at all."""
    data = json.loads(sys.stdin.read())
    tool_input = data.get("tool_input", {})
    if data.get("tool_name") in EDIT_TOOLS:
        on_edit(tool_input.get("file_path", ""), data["session_id"])
    elif data.get("tool_name") == "Bash":
        on_bash(tool_input.get("command", ""), data.get("cwd") or ".")


if __name__ == "__main__":
    main()
