"""Tests for plugins/delegation-hook/hooks/delegation_hook.py.

Decisions are driven through `main()` over a synthesised PreToolUse payload, so each case asserts
the JSON Claude Code actually receives — including the two shapes that carry no decision at all
(a bare `additionalContext`, and total silence), which is where a regression would be invisible.
"""

import io
import json
import os
import time

import delegation_hook
import pytest
from delegation_hook import (
    _RECORD_SIZE,
    BUDGET,
    TIME_BUDGET_MINUTES,
    TIME_BUDGET_SECONDS,
    TIME_WARN_FROM_SECONDS,
    WARN_FROM,
    _record,
    record_call,
)

SUBAGENT = "ad315c8dd518d2aec"


def via_main(monkeypatch, capsys, **payload):
    """Run the hook over a PreToolUse payload; returns the emitted hookSpecificOutput or None."""
    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps(payload)))
    delegation_hook.main()
    out = capsys.readouterr().out
    return json.loads(out)["hookSpecificOutput"] if out.strip() else None


def spawn(background):
    """A main-thread `Agent` payload — no agent_id, since the main thread never carries one."""
    tool_input = {"prompt": "read auth.py, answer whether the token is refreshed"}
    if background is not None:
        tool_input["run_in_background"] = background
    return {"tool_name": "Agent", "tool_input": tool_input}


def burn(agent_id, calls):
    """Spend `calls` of `agent_id`'s budget without going through the emit path."""
    for _ in range(calls):
        record_call(agent_id)


def backdate(agent_id, seconds):
    """Rewrite `agent_id`'s first record so its run started `seconds` ago."""
    counter = delegation_hook._counter_path(agent_id)
    started = _record(time.time() - seconds)
    counter.write_bytes(started + counter.read_bytes()[_RECORD_SIZE:])


# ── subagents must run in the background ─────────────────────────────────────


def test_foreground_spawn_denied(monkeypatch, capsys):
    """A foreground agent freezes the session for its whole run; the harness notifies instead."""
    out = via_main(monkeypatch, capsys, **spawn(background=False))
    assert out["permissionDecision"] == "deny"
    assert "run_in_background: false" in out["permissionDecisionReason"]


@pytest.mark.parametrize("background", [True, None])
def test_background_spawn_allowed_with_slicing_rule(monkeypatch, capsys, background):
    """Explicit background and the omitted default (which detaches) are both the wanted shape."""
    out = via_main(monkeypatch, capsys, **spawn(background))
    assert "permissionDecision" not in out  # never allow: a sibling hook's verdict must survive
    assert f"{BUDGET} tool calls" in out["additionalContext"]


@pytest.mark.parametrize("background", [True, None])
def test_background_spawn_includes_model_routing_guide(monkeypatch, capsys, background):
    """The spawner picks the model per slice; the guide names all three tiers it can route to."""
    context = via_main(monkeypatch, capsys, **spawn(background))["additionalContext"]
    for tier in ("`opus`", "`sonnet`", "`haiku`"):
        assert tier in context


@pytest.mark.parametrize("background", [True, None])
def test_background_spawn_puts_a_short_answer_on_the_caller(monkeypatch, capsys, background):
    """The caller sized the slice knowing the budget, so a half-done agent is its miss to fix."""
    context = via_main(monkeypatch, capsys, **spawn(background))["additionalContext"]
    assert "your slicing, not the agent falling short" in context


def test_the_denial_does_not_send_the_agent_to_poll(monkeypatch, capsys):
    """Waiting in a loop is the failure this plugin exists to prevent, in either direction."""
    reason = via_main(monkeypatch, capsys, **spawn(background=False))["permissionDecisionReason"]
    assert "poll" in reason.lower()


def test_non_agent_tool_in_main_thread_is_silent(monkeypatch, capsys):
    assert via_main(monkeypatch, capsys, tool_name="Bash", tool_input={"command": "ls"}) is None


# ── the subagent tool-call budget ────────────────────────────────────────────


def test_early_calls_are_silent(monkeypatch, capsys):
    burn(SUBAGENT, WARN_FROM - 2)
    out = via_main(monkeypatch, capsys, tool_name="Read", tool_input={}, agent_id=SUBAGENT)
    assert out is None


def test_warning_starts_at_the_threshold(monkeypatch, capsys):
    burn(SUBAGENT, WARN_FROM - 1)
    out = via_main(monkeypatch, capsys, tool_name="Read", tool_input={}, agent_id=SUBAGENT)
    assert "permissionDecision" not in out
    assert f"{WARN_FROM}/{BUDGET} tool calls" in out["additionalContext"]


def test_last_call_within_budget_still_runs(monkeypatch, capsys):
    burn(SUBAGENT, BUDGET - 1)
    out = via_main(monkeypatch, capsys, tool_name="Read", tool_input={}, agent_id=SUBAGENT)
    assert "permissionDecision" not in out


def test_call_past_the_budget_is_denied(monkeypatch, capsys):
    burn(SUBAGENT, BUDGET)
    out = via_main(monkeypatch, capsys, tool_name="Read", tool_input={}, agent_id=SUBAGENT)
    assert out["permissionDecision"] == "deny"
    assert "Write your final message now" in out["permissionDecisionReason"]


def test_budget_is_per_agent(monkeypatch, capsys):
    burn(SUBAGENT, BUDGET)
    out = via_main(monkeypatch, capsys, tool_name="Read", tool_input={}, agent_id="other-agent")
    assert out is None


def test_main_thread_has_no_budget(monkeypatch, capsys):
    """The main thread carries no agent_id, so no counter exists to exhaust."""
    emitted = [via_main(monkeypatch, capsys, tool_name="Read", tool_input={}) for _ in range(BUDGET + 5)]
    assert emitted == [None] * (BUDGET + 5)


def test_spent_budget_denies_a_spawn_too(monkeypatch, capsys):
    """An out-of-budget subagent can't delegate its way out — the deny outranks the slicing rule."""
    burn(SUBAGENT, BUDGET)
    out = via_main(monkeypatch, capsys, agent_id=SUBAGENT, **spawn(background=True))
    assert out["permissionDecision"] == "deny"
    assert "budget is spent" in out["permissionDecisionReason"]


def test_warning_and_slicing_rule_are_delivered_together(monkeypatch, capsys):
    burn(SUBAGENT, WARN_FROM - 1)
    out = via_main(monkeypatch, capsys, agent_id=SUBAGENT, **spawn(background=True))
    assert f"{WARN_FROM}/{BUDGET} tool calls" in out["additionalContext"]
    assert f"This subagent gets {BUDGET} tool calls" in out["additionalContext"]


# ── the subagent wall-clock budget ───────────────────────────────────────────


def test_time_warning_starts_at_the_threshold(monkeypatch, capsys):
    burn(SUBAGENT, 1)
    backdate(SUBAGENT, TIME_WARN_FROM_SECONDS)
    out = via_main(monkeypatch, capsys, tool_name="Read", tool_input={}, agent_id=SUBAGENT)
    assert "permissionDecision" not in out
    assert f"5.0/{TIME_BUDGET_MINUTES} minutes used" in out["additionalContext"]


def test_call_past_the_time_budget_is_denied(monkeypatch, capsys):
    """Few slow calls instead of many fast ones — the time axis catches what the count misses."""
    burn(SUBAGENT, 1)
    backdate(SUBAGENT, TIME_BUDGET_SECONDS + 1)
    out = via_main(monkeypatch, capsys, tool_name="Read", tool_input={}, agent_id=SUBAGENT)
    assert out["permissionDecision"] == "deny"
    assert "wall-clock budget is spent" in out["permissionDecisionReason"]
    assert "Write your final message now" in out["permissionDecisionReason"]


def test_fresh_agent_is_nowhere_near_the_time_budget(monkeypatch, capsys):
    burn(SUBAGENT, 1)
    out = via_main(monkeypatch, capsys, tool_name="Read", tool_input={}, agent_id=SUBAGENT)
    assert out is None


# ── the counter store ────────────────────────────────────────────────────────


def test_counter_survives_separate_processes():
    """Each hook invocation is its own process, so the count has to live entirely on disk."""
    assert [record_call(SUBAGENT)[0] for _ in range(3)] == [1, 2, 3]


def test_elapsed_is_measured_from_the_first_call():
    burn(SUBAGENT, 2)
    backdate(SUBAGENT, 90)
    used, elapsed = record_call(SUBAGENT)
    assert used == 3
    assert 90 <= elapsed < 95


def test_v1_counter_restarts_the_count():
    """A bare-dots file from the pre-timestamp hook restarts rather than crashing every call."""
    delegation_hook.STATE_DIR.mkdir(parents=True)
    delegation_hook._counter_path(SUBAGENT).write_bytes(b"." * 20)
    used, elapsed = record_call(SUBAGENT)
    assert used == 1
    assert 0 <= elapsed < 5


def test_agent_id_cannot_escape_the_state_dir(state_dir):
    record_call("../../escaped")
    assert [p.name for p in state_dir.iterdir()] == ["______escaped.calls"]


def test_stale_counters_are_swept_on_a_fresh_agent(state_dir):
    burn("finished-yesterday", 4)
    stale = state_dir / "finished-yesterday.calls"
    old = time.time() - delegation_hook.STALE_AFTER_SECONDS - 1
    os.utime(stale, (old, old))

    record_call("just-started")

    assert not stale.exists()
    assert (state_dir / "just-started.calls").exists()
