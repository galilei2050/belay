#!/usr/bin/env python3
"""Stop hook: refuse to end the turn while the work is unpushed or unproposed.

The nudge from `nudge_after_git.py` is advisory, and advisory text gets skipped. This is the
backstop — the turn does not end while commits sit on this machine only, or sit on the remote
with no PR in front of them.

It refuses once per (HEAD, step). A push turns "push me" into "open a PR", which is a different
demand and earns its own refusal; an agent that answers a refusal in words and stops again is
let through rather than looped.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

from branch_state import git, next_step

STATE_PATH = Path.home() / ".claude" / "pr-flow" / "refused.json"

# Enough keys to cover a branch's worth of states; the file is a loop-breaker, not a history.
_MAX_KEYS_PER_REPO = 32


def already_refused(repo: str, key: str) -> bool:
    """True iff this exact (HEAD, step) was already refused once."""
    if not STATE_PATH.exists():
        return False
    return key in json.loads(STATE_PATH.read_text()).get(repo, [])


def record_refused(repo: str, key: str) -> None:
    """Remember the refusal, so the same one cannot fire twice."""
    state = json.loads(STATE_PATH.read_text()) if STATE_PATH.exists() else {}
    state[repo] = [*state.get(repo, []), key][-_MAX_KEYS_PER_REPO:]
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    STATE_PATH.write_text(json.dumps(state))


def refusal_for(data: dict[str, object]) -> str | None:
    """The reason to send the agent back, or None to let the turn end."""
    if data.get("stop_hook_active"):
        return None
    cwd = str(data.get("cwd") or ".")
    step = next_step(cwd, after_push=False)
    if step is None or not step.refusal:
        return None

    toplevel = git(cwd, "rev-parse", "--show-toplevel")
    head = git(cwd, "rev-parse", "HEAD")
    if toplevel is None or head is None:
        return None
    key = f"{head}:{step.kind}"
    if already_refused(toplevel, key):
        return None
    record_refused(toplevel, key)
    return step.refusal


def main() -> None:
    """Stop entry point: block the turn once, or stay out of the way."""
    data = json.loads(sys.stdin.read())
    if data.get("hook_event_name") != "Stop":
        return
    refusal = refusal_for(data)
    if refusal is None:
        return
    sys.stdout.write(json.dumps({"decision": "block", "reason": refusal}) + "\n")


if __name__ == "__main__":
    main()
