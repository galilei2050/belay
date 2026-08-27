#!/usr/bin/env python3
"""PreToolUse hook: after a `git commit`, put the reviewer panel on the agent's desk.

Advisory by design — it never blocks the commit. The hook emits only
`hookSpecificOutput.additionalContext` and no `permissionDecision`, so the call still
goes through the normal permission flow and acl-hook keeps the last word. Claude Code
reads `additionalContext` on the *next* model request, i.e. after the commit already
landed, so the roster points the panel at `HEAD` rather than at the index.

One job: decide whether this Bash call is a real commit worth reviewing, and hand the
agent the roster. It reviews nothing itself and knows nothing about the reviewers
beyond their names.
"""

from __future__ import annotations

import hashlib
import json
import re
import shutil
import subprocess
import sys
from pathlib import Path
from typing import NamedTuple

STATE_PATH = Path.home() / ".claude" / "review-panel" / "reviewed.json"
GIT = shutil.which("git")

# Under this many changed lines a round costs more than the commit is worth — eight subagents
# over a diff its author can hold in their head. `git commit` is the moment the size is known.
MIN_CHANGED_LINES = 64

# The panel. They run in parallel, so this order only sets the reading order of the merged
# report — semantic findings first, because a wrong answer outranks a long function.
REVIEWERS = (
    "correctness-reviewer",
    "integration-reviewer",
    "test-integrity-reviewer",
    "explicitness-reviewer",
    "duplication-reviewer",
    "bloat-reviewer",
    "solid-reviewer",
    "comments-reviewer",
)

# A Bash call is a list of commands, and each is judged on its own: a newline separates two
# commands exactly like `;` does, and a flag belongs to the command it was written on.
_SEGMENT_SPLIT_RE = re.compile(r"[;&|\n()]+")
# Quoted spans are blanked before any flag is read: `--dry-run` inside a commit message is prose.
_QUOTED_RE = re.compile(r"'[^']*'|\"[^\"]*\"")
# `-C dir` / `--git-dir` / `--work-tree` point git at another repository, while the diff this
# hook measures comes from the payload's `cwd`. Reviewing one repo's commit against another's
# index is worse than staying quiet. Matched only in the span *before* the subcommand, where
# git's own options live: `git commit -C HEAD~1` reuses a message and commits right here.
_OTHER_REPO_RE = re.compile(r"(?<![\w-])(?:-C|--git-dir|--work-tree)(?![\w-])")
# Leading `-`/`--` options belong to git itself (--no-pager); the subcommand is the first bare word.
_GIT_COMMIT_RE = re.compile(r"^\s*git\b(?:\s+-{1,2}\S+(?:\s+\S+)?)*\s+commit\b")
_DRY_RUN_RE = re.compile(r"(?<![\w-])--dry-run(?![\w-])")
# `-a` / `-am` stage tracked files at commit time, so the index is empty until then and
# the review scope has to come from the worktree instead. `(?!-)` keeps `--amend` out.
_STAGE_ALL_RE = re.compile(r"(?<![\w-])(?:-(?!-)\w*a\w*|--all)(?![\w-])")

_PROMPT = """\
You just ran `git commit` — {lines} changed lines across {files} file(s). Before moving on, put
that commit through the review panel.

Dispatch all {count} reviewers **in a single message** so they run in parallel. They run in the
background and each report arrives as a notification — carry on with something else meanwhile,
and never poll for them. Each is read-only and reviews the same scope: the diff of the commit
that just landed (`git show HEAD`) — plus whatever surrounding files it needs to read for context.

{roster}

Then:
- Merge their findings. Drop anything a reviewer could not point at a concrete line.
- Fix what survives, and commit the fixes. Do not amend unless the commit is unpushed
  and the fix is trivial.
- If nothing survives, say so in one line and move on.

**Dispatch unless this commit is nothing but the panel's own corrections.** A round costs
{count} subagents of the user's money, and exactly one kind of commit skips it: one where every
hunk is traceable to a finding from the round you just ran. That is a test on the content, not
on the order — a commit that introduces a type, a branch, a file, an interface or a behavior
the panel has not read fails it, however recently the panel ran, and {lines} changed lines
across {files} file(s) is the size you are weighing against that bar. Say in one line which of
the two this commit is before you decide, and if it is the corrections one, dispatch nobody.
A panel handed its own corrections finds fresh wording to object to indefinitely, and a finding
you already rejected does not get a second opinion.

The panel is advisory — none of this blocks the commit that already happened."""


def committing_segment(command: str) -> str | None:
    """The segment of this Bash call that commits to the session's own repo, or None.

    The segment, not the whole call: `--dry-run` and `-a` belong to the command they were
    written on, and every later read (`_STAGE_ALL_RE`) has to be scoped the same way.
    """
    blanked = _QUOTED_RE.sub(" ", command)
    for segment in _SEGMENT_SPLIT_RE.split(blanked):
        match = _GIT_COMMIT_RE.search(segment)
        if match is None or _DRY_RUN_RE.search(segment):
            continue
        if _OTHER_REPO_RE.search(segment[: match.end()]):
            continue
        return segment
    return None


def _git(cwd: str, *args: str) -> str | None:
    """Run a read-only git command, or None if git is absent or refuses (not a repo, bad rev)."""
    if GIT is None:
        return None
    # S603: every `args` at every call site is a string literal, the binary is resolved by
    # `shutil.which`, and there is no shell — the only caller-supplied value is `cwd`.
    result = subprocess.run((GIT, *args), cwd=cwd, capture_output=True, text=True, check=False)  # noqa: S603
    return result.stdout if result.returncode == 0 else None


def changed_lines(diff: str) -> int:
    """Added plus removed lines in a unified diff, not counting the `+++`/`--- ` file headers."""
    body = (line for line in diff.splitlines() if not line.startswith(("+++ ", "--- ")))
    return sum(1 for line in body if line.startswith(("+", "-")))


def changed_files(diff: str) -> int:
    """Files touched by a unified diff, counted from the `diff --git` header of each one."""
    return sum(1 for line in diff.splitlines() if line.startswith("diff --git "))


class Scope(NamedTuple):
    """The code about to be committed: what the digest is over, and how big it measures.

    The size travels with the digest because the prompt quotes it back. An agent deciding
    whether a commit is "only the last round's fixes" is talking itself out of the whole
    panel, and the one fact that settles it is how much code is actually in front of it.
    """

    digest: str
    lines: int
    files: int


def review_scope(cwd: str, segment: str) -> Scope | None:
    """The code about to be committed, or None if it is not worth a round.

    Takes the committing segment rather than the whole Bash call: `-a` in an earlier command
    (`git stash -a && git commit -m x`) says nothing about what this commit stages.

    The digest is what makes the hook idempotent: a commit rejected by pre-commit and
    retried carries the same content, and re-dispatching the whole panel over it is pure
    waste. Once the agent fixes something the content changes and the panel runs again.
    """
    diff = _git(cwd, "diff", "HEAD" if _STAGE_ALL_RE.search(segment) else "--cached")
    if not diff:
        return None
    lines = changed_lines(diff)
    if lines < MIN_CHANGED_LINES:
        return None
    return Scope(hashlib.sha256(diff.encode()).hexdigest(), lines, changed_files(diff))


def already_reviewed(repo: str, digest: str) -> bool:
    """True iff the panel was already dispatched over this exact content in this repo."""
    if not STATE_PATH.exists():
        return False
    return json.loads(STATE_PATH.read_text()).get(repo) == digest


def record_reviewed(repo: str, digest: str) -> None:
    """Remember that the panel was dispatched over this content, so a retry stays silent."""
    state = json.loads(STATE_PATH.read_text()) if STATE_PATH.exists() else {}
    state[repo] = digest
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    STATE_PATH.write_text(json.dumps(state))


def build_prompt(scope: Scope) -> str:
    """The advisory text handed to the agent: the roster plus what to do with its findings."""
    roster = "\n".join(f"- `review-panel:{name}`" for name in REVIEWERS)
    return _PROMPT.format(count=len(REVIEWERS), roster=roster, lines=scope.lines, files=scope.files)


def main() -> None:
    """PreToolUse entry point: emit the panel roster, or nothing at all."""
    data = json.loads(sys.stdin.read())
    if data.get("tool_name") != "Bash":
        return
    command = data.get("tool_input", {}).get("command", "")
    segment = committing_segment(command)
    if segment is None:
        return

    cwd = data.get("cwd") or "."
    toplevel = _git(cwd, "rev-parse", "--show-toplevel")
    if toplevel is None:
        return  # not a git repo — the commit will fail on its own
    scope = review_scope(cwd, segment)
    if scope is None:
        return
    repo = toplevel.strip()
    if already_reviewed(repo, scope.digest):
        return
    record_reviewed(repo, scope.digest)

    sys.stdout.write(
        json.dumps(
            {
                "hookSpecificOutput": {
                    "hookEventName": "PreToolUse",
                    "additionalContext": build_prompt(scope),
                }
            }
        )
        + "\n"
    )


if __name__ == "__main__":
    main()
