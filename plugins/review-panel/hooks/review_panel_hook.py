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

# `git -C dir commit`, `foo && git commit -m x`, `git commit`. Leading `-`/`--` options
# belong to git itself (-C, --no-pager); the subcommand is the first bare word.
_GIT_COMMIT_RE = re.compile(r"(?:^|[;&|(]|&&)\s*git\b(?:\s+-{1,2}\S+(?:\s+\S+)?)*\s+commit\b")
_DRY_RUN_RE = re.compile(r"(?<![\w-])--dry-run(?![\w-])")
# `-a` / `-am` stage tracked files at commit time, so the index is empty until then and
# the review scope has to come from the worktree instead. `(?!-)` keeps `--amend` out.
_STAGE_ALL_RE = re.compile(r"(?<![\w-])(?:-(?!-)\w*a\w*|--all)(?![\w-])")

_PROMPT = """\
You just ran `git commit`. Before moving on, put that commit through the review panel.

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

**Dispatch only over changes the panel has not seen.** A round costs {count} subagents of
the user's money, so spend it on substantial new work — new code, a behavior change, a file
the panel has not read. A commit that only applies the findings from the round you just ran
is not that: say so in one line and dispatch nobody. A panel handed its own corrections
finds fresh wording to object to indefinitely, and a finding you already rejected does not
get a second opinion.

The panel is advisory — none of this blocks the commit that already happened."""


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


def changed_lines(diff: str) -> int:
    """Added plus removed lines in a unified diff, not counting the `+++`/`--- ` file headers."""
    body = (line for line in diff.splitlines() if not line.startswith(("+++ ", "--- ")))
    return sum(1 for line in body if line.startswith(("+", "-")))


def review_scope_digest(cwd: str, command: str) -> str | None:
    """Digest of the code about to be committed, or None if it is not worth a round.

    The digest is what makes the hook idempotent: a commit rejected by pre-commit and
    retried carries the same content, and re-dispatching the whole panel over it is pure
    waste. Once the agent fixes something the content changes and the panel runs again.
    """
    diff = _git(cwd, "diff", "HEAD" if _STAGE_ALL_RE.search(command) else "--cached")
    if not diff or changed_lines(diff) < MIN_CHANGED_LINES:
        return None
    return hashlib.sha256(diff.encode()).hexdigest()


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


def build_prompt() -> str:
    """The advisory text handed to the agent: the roster plus what to do with its findings."""
    roster = "\n".join(f"- `review-panel:{name}`" for name in REVIEWERS)
    return _PROMPT.format(count=len(REVIEWERS), roster=roster)


def main() -> None:
    """PreToolUse entry point: emit the panel roster, or nothing at all."""
    data = json.loads(sys.stdin.read())
    if data.get("tool_name") != "Bash":
        return
    command = data.get("tool_input", {}).get("command", "")
    if not is_reviewable_commit(command):
        return

    cwd = data.get("cwd") or "."
    toplevel = _git(cwd, "rev-parse", "--show-toplevel")
    if toplevel is None:
        return  # not a git repo — the commit will fail on its own
    digest = review_scope_digest(cwd, command)
    if digest is None:
        return
    repo = toplevel.strip()
    if already_reviewed(repo, digest):
        return
    record_reviewed(repo, digest)

    sys.stdout.write(
        json.dumps(
            {
                "hookSpecificOutput": {
                    "hookEventName": "PreToolUse",
                    "additionalContext": build_prompt(),
                }
            }
        )
        + "\n"
    )


if __name__ == "__main__":
    main()
