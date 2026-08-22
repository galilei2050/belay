#!/usr/bin/env python3
"""Delegation gate for Claude Code subagents — background only, under a hard tool-call budget.

Two ways handing work to a subagent goes wrong, one plugin:

* **Blocking.** A foreground subagent freezes the caller for its whole run — minutes during which
  the session can do nothing else, including the parts of the task that never needed the subagent's
  answer. The harness re-invokes the caller when a background agent finishes, so the result arrives
  without anyone waiting for it, and several agents can run at once. Denied at the `Agent` call,
  where dropping `run_in_background: false` fixes it.
* **Oversized.** An open-ended slice makes the subagent grind: it keeps exploring, its window fills
  with its own tool output, and it answers from that. Bounded by counting its tool calls and cutting
  them off at `BUDGET`, which forces an answer from what it already has and names what it missed.

The spawning agent is handed the budget at spawn time, so it can size the slice to fit rather than
discover the ceiling by hitting it.

Only ever emits `deny` or a bare `additionalContext` — never `allow`, so this hook can't override a
sibling hook's verdict on the same call (same discipline as branch-guard-hook).
"""

from __future__ import annotations

import json
import re
import sys
import time
from pathlib import Path
from typing import NamedTuple

# 30 comes from the user. Measured against 59 real subagent runs on this machine (2026-08-22):
# median 18 tool calls, p90 38, max 58 — so it cuts the slowest ~19%, which is the population that
# bogs down. A truncated agent still reports; an unbounded one still hasn't answered.
BUDGET = 30

# Warn only once the end is close enough to change behaviour. Earlier is noise in every subagent's
# context; later leaves no room to converge.
WARN_FROM = 20

STATE_DIR = Path.home() / ".claude" / "state" / "delegation-hook"

# Counters outlive the agent that wrote them (there is no end-of-agent hook to clean up), so a run
# older than this is finished by definition and gets swept on the next agent's first call.
STALE_AFTER_SECONDS = 24 * 60 * 60

_UNSAFE_IN_NAME = re.compile(r"[^A-Za-z0-9_-]")

_FOREGROUND_DENIED = (
    "Subagents run in the background — drop `run_in_background: false` and call it again. A "
    "foreground agent freezes this session for its entire run, and nothing about waiting makes its "
    "answer better. Detached, the harness re-invokes you the moment it finishes, several agents can "
    "run at once, and you keep working on the parts of the task that never needed its answer. Do "
    "not poll its output file — the notification is the signal, and a wait loop is the one thing "
    "that turns a background agent back into a blocking one."
)

_SLICE_THE_TASK = (
    f"This subagent gets {BUDGET} tool calls. At {BUDGET} its tools are blocked and it has to answer "
    "from whatever it has by then, so the slice has to fit the budget:\n"
    '- One named deliverable per agent. "Investigate X" has no end; "read A, B and C, answer Q" does.\n'
    "- Hand over what you already know — exact paths, symbols, commands — so its budget goes on "
    "reading, not on re-discovering what you could have told it.\n"
    "- Scout yourself, delegate the reading: locating the files costs you a few calls and costs it "
    "most of its budget.\n"
    f"- Work worth more than ~{BUDGET} calls is several agents with one slice each, not one agent told "
    "to hurry."
)

_BUDGET_SPENT = (
    f"Your tool-call budget is spent ({BUDGET} calls) — every further tool call is blocked, and "
    "retrying will not unblock it. Write your final message now: give the answer from what you already "
    "have, and state plainly which parts you did not get to, so the caller can send a follow-up agent "
    "for exactly those."
)


def _counter_path(agent_id: str) -> Path:
    """Counter file for `agent_id`, with the id reduced to characters that can only be a filename."""
    return STATE_DIR / f"{_UNSAFE_IN_NAME.sub('_', agent_id)}.calls"


def _sweep_stale() -> None:
    cutoff = time.time() - STALE_AFTER_SECONDS
    for counter in STATE_DIR.glob("*.calls"):
        if counter.stat().st_mtime < cutoff:
            counter.unlink(missing_ok=True)  # another agent sweeping the same dir may have won


def record_call(agent_id: str) -> int:
    """Count one tool call for `agent_id` and return its running total, 1 for the first.

    One byte appended per call rather than a read-modify-write of a number: parallel tool calls in a
    single turn run as concurrent hook processes, and an O_APPEND write is the only shape that can't
    lose one of them to a lost update.
    """
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    counter = _counter_path(agent_id)
    with counter.open("ab") as handle:
        handle.write(b".")
    used = counter.stat().st_size
    if used == 1:
        _sweep_stale()  # first call of a fresh agent: the cheapest moment to pay for the cleanup
    return used


class Verdict(NamedTuple):
    """What this hook has to say about one tool call: at most one deny, any number of nudges."""

    deny: str | None
    context: list[str]


def judge(data: dict[str, object]) -> Verdict:
    """Decide one PreToolUse payload.

    Both rules are evaluated on every call because they can fire together — a subagent that spawns
    its own subagent is spending budget *and* delegating.
    """
    deny: str | None = None
    context: list[str] = []

    tool_input = data.get("tool_input")
    if data.get("tool_name") == "Agent" and isinstance(tool_input, dict):
        # Absent means background: the Agent tool detaches by default, so only an explicit false
        # counts as foreground — and that is the one shape denied.
        if tool_input.get("run_in_background") is False:
            deny = _FOREGROUND_DENIED
        else:
            context.append(_SLICE_THE_TASK)

    agent_id = data.get("agent_id")
    if isinstance(agent_id, str):  # absent in the main thread — the budget is a subagent rule
        used = record_call(agent_id)
        if used > BUDGET:
            deny = _BUDGET_SPENT
        elif used >= WARN_FROM:
            context.append(
                f"Tool-call budget: {used}/{BUDGET} used, {BUDGET - used} left. At {BUDGET} every tool "
                "is blocked and you answer from what you have — stop widening the search, start writing."
            )
    return Verdict(deny, context)


def _emit(verdict: Verdict) -> None:
    output: dict[str, object] = {"hookEventName": "PreToolUse"}
    if verdict.deny is not None:
        output["permissionDecision"] = "deny"
        output["permissionDecisionReason"] = verdict.deny
    elif verdict.context:
        output["additionalContext"] = "\n\n".join(verdict.context)
    else:
        return
    sys.stdout.write(json.dumps({"hookSpecificOutput": output}) + "\n")


def main() -> None:
    """PreToolUse entry point: read the stdin payload, emit a deny, a context nudge, or nothing."""
    _emit(judge(json.loads(sys.stdin.read())))


if __name__ == "__main__":
    main()
