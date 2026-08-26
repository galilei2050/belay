#!/usr/bin/env python3
"""Delegation gate for Claude Code subagents — background only, under hard tool-call and time budgets.

Two ways handing work to a subagent goes wrong, one plugin:

* **Blocking.** A foreground subagent freezes the caller for its whole run — minutes during which
  the session can do nothing else, including the parts of the task that never needed the subagent's
  answer. The harness re-invokes the caller when a background agent finishes, so the result arrives
  without anyone waiting for it, and several agents can run at once. Denied at the `Agent` call,
  where dropping `run_in_background: false` fixes it.
* **Oversized.** An open-ended slice makes the subagent grind: it keeps exploring, its window fills
  with its own tool output, and it answers from that. Bounded on both axes — `BUDGET` tool calls and
  `TIME_BUDGET_SECONDS` of wall-clock — with tools cut off at either limit, which forces an answer
  from what it already has and names what it missed.

The spawning agent is handed the budgets at spawn time, so it can size the slice to fit rather than
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

# 7 minutes comes from the user. Measured against 727 real subagent runs on this machine
# (2026-08-22): median 3.7 min, p90 14.2 min — so it cuts the slowest ~23%, the same population the
# call budget targets from the other axis: few slow calls instead of many fast ones. Wall-clock, so
# a machine suspend mid-run spends it; an agent resumed hours later is told to wrap up, not to
# resume grinding.
TIME_BUDGET_MINUTES = 7
TIME_BUDGET_SECONDS = TIME_BUDGET_MINUTES * 60

TIME_WARN_FROM_SECONDS = 5 * 60

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
    f"This subagent gets {BUDGET} tool calls and {TIME_BUDGET_MINUTES} minutes. At either "
    "limit its tools are blocked and it has to answer from whatever it has by then, so the slice "
    "has to fit the budget:\n"
    '- One named deliverable per agent. "Investigate X" has no end; "read A, B and C, answer Q" does.\n'
    "- Hand over what you already know — exact paths, symbols, commands — so its budget goes on "
    "reading, not on re-discovering what you could have told it.\n"
    "- Scout yourself, delegate the reading: locating the files costs you a few calls and costs it "
    "most of its budget.\n"
    f"- Work worth more than ~{BUDGET} calls is several agents with one slice each, not one agent told "
    "to hurry."
)

# Grounded in Anthropic's model guidance and subagent-routing consensus (2026-08): route by task
# shape, don't default upward. Marketplace agents pin `model: opus`; the spawner overrides per call.
_PICK_THE_MODEL = (
    "Pick the model for the slice — the `model` param overrides the agent's default:\n"
    "- `opus`: review, hypothesis falsification, hard debugging, cross-file synthesis. Weak spot: "
    "overkill for routine work — you pay opus rates for reading files.\n"
    "- `sonnet`: writing code and tests, ordinary analysis, at ~half the opus cost. Weak spot: "
    "misses subtle cross-cutting bugs.\n"
    "- `haiku`: search, file discovery, mechanical checks, ~5x cheaper than opus. Weak spot: "
    "shallow on anything needing judgment."
)

_WRAP_UP = (
    "every further tool call is blocked, and retrying will not unblock it. Write your final message "
    "now: give the answer from what you already have, and state plainly which parts you did not get "
    "to, so the caller can send a follow-up agent for exactly those."
)

_BUDGET_SPENT = f"Your tool-call budget is spent ({BUDGET} calls) — {_WRAP_UP}"

_TIME_SPENT = f"Your wall-clock budget is spent ({TIME_BUDGET_MINUTES} minutes) — {_WRAP_UP}"


def _counter_path(agent_id: str) -> Path:
    """Counter file for `agent_id`, with the id reduced to characters that can only be a filename."""
    return STATE_DIR / f"{_UNSAFE_IN_NAME.sub('_', agent_id)}.calls"


def _sweep_stale() -> None:
    cutoff = time.time() - STALE_AFTER_SECONDS
    for counter in STATE_DIR.glob("*.calls"):
        if counter.stat().st_mtime < cutoff:
            counter.unlink(missing_ok=True)  # another agent sweeping the same dir may have won


def _record(timestamp: float) -> bytes:
    """One counter record — fixed width, so file size // _RECORD_SIZE is the call count."""
    return b"%017.6f\n" % timestamp


_RECORD_SIZE = len(_record(0.0))


class Spend(NamedTuple):
    """One agent's spend so far: tool calls made, and seconds since its first."""

    used: int
    elapsed_seconds: float


def record_call(agent_id: str) -> Spend:
    """Count one tool call for `agent_id`; returns its running spend, `used == 1` for the first.

    One fixed-width timestamp appended per call rather than a read-modify-write: parallel tool
    calls in a single turn run as concurrent hook processes, and an O_APPEND write is the only
    shape that can't lose one of them to a lost update. The first record doubles as the agent's
    start time — that is what the wall-clock budget measures against.
    """
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    counter = _counter_path(agent_id)
    record = _record(time.time())
    with counter.open("ab") as handle:
        handle.write(record)
    with counter.open("rb") as handle:
        first = handle.read(_RECORD_SIZE)
    try:
        started = float(first.decode("ascii"))
    except ValueError:
        # A counter written by v1 of this hook (one bare "." per call) — restart it. Costs one
        # running agent one budget reset during the migration; v1 files sweep out within 24h.
        counter.unlink(missing_ok=True)
        return record_call(agent_id)
    used = counter.stat().st_size // _RECORD_SIZE
    if used == 1:
        _sweep_stale()  # first call of a fresh agent: the cheapest moment to pay for the cleanup
    # Elapsed is stored-minus-stored, not raw-minus-stored: %017.6f rounds to the microsecond, so
    # a raw `now` can land just below its own record and make a first call's elapsed negative.
    return Spend(used, float(record.decode("ascii")) - started)


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
            context.append(_PICK_THE_MODEL)

    agent_id = data.get("agent_id")
    if isinstance(agent_id, str):  # absent in the main thread — the budgets are a subagent rule
        used, elapsed = record_call(agent_id)
        if used > BUDGET:
            deny = _BUDGET_SPENT
        elif elapsed > TIME_BUDGET_SECONDS:
            deny = _TIME_SPENT
        elif used >= WARN_FROM or elapsed >= TIME_WARN_FROM_SECONDS:
            context.append(
                f"Budget: {used}/{BUDGET} tool calls and {elapsed / 60:.1f}/{TIME_BUDGET_MINUTES} "
                "minutes used. At either limit every tool is blocked and you answer from what you "
                "have — stop widening the search, start writing."
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
