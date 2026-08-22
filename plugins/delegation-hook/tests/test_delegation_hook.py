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
from delegation_hook import BUDGET, WARN_FROM, record_call

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


# ── subagents must run in the foreground ─────────────────────────────────────


@pytest.mark.parametrize("background", [True, None])
def test_background_spawn_denied(monkeypatch, capsys, background):
    """Explicit background and the omitted default (which detaches) both deny."""
    out = via_main(monkeypatch, capsys, **spawn(background))
    assert out["permissionDecision"] == "deny"
    assert "run_in_background: false" in out["permissionDecisionReason"]


def test_foreground_spawn_allowed_with_slicing_rule(monkeypatch, capsys):
    out = via_main(monkeypatch, capsys, **spawn(background=False))
    assert "permissionDecision" not in out  # never allow: a sibling hook's verdict must survive
    assert f"{BUDGET} tool calls" in out["additionalContext"]


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
    assert f"{WARN_FROM}/{BUDGET} used, {BUDGET - WARN_FROM} left" in out["additionalContext"]


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
    out = via_main(monkeypatch, capsys, agent_id=SUBAGENT, **spawn(background=False))
    assert out["permissionDecision"] == "deny"
    assert "budget is spent" in out["permissionDecisionReason"]


def test_warning_and_slicing_rule_are_delivered_together(monkeypatch, capsys):
    burn(SUBAGENT, WARN_FROM - 1)
    out = via_main(monkeypatch, capsys, agent_id=SUBAGENT, **spawn(background=False))
    assert f"{WARN_FROM}/{BUDGET} used" in out["additionalContext"]
    assert f"This subagent gets {BUDGET} tool calls" in out["additionalContext"]


# ── the counter store ────────────────────────────────────────────────────────


def test_counter_survives_separate_processes():
    """Each hook invocation is its own process, so the count has to live entirely on disk."""
    assert [record_call(SUBAGENT) for _ in range(3)] == [1, 2, 3]


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
