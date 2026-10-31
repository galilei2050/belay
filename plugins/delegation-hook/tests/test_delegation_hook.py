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
    CONTEXT_BUDGET,
    CONTEXT_WARN_FROM,
    TIME_BUDGET_MINUTES,
    TIME_BUDGET_SECONDS,
    TIME_WARN_FROM_SECONDS,
    _record,
    record_call,
)

SUBAGENT = "ad315c8dd518d2aec"
STARTUP_TOKENS = 67_000


@pytest.fixture
def session(tmp_path):
    """The parent session's transcript path, as the harness puts it in `transcript_path`."""
    return tmp_path / "projects" / "-home-u-repo" / "4f1c2a9e-session.jsonl"


def at_context(session, agent_id, tokens):
    """Write `agent_id`'s transcript so its latest model call carried `tokens`; returns call kwargs.

    Shaped like a real one: most of the context arrives as cache reads, and an earlier, smaller
    turn sits above the latest so reading the wrong end of the file would show.
    """
    transcript = session.parent / session.stem / "subagents" / f"agent-{agent_id}.jsonl"
    transcript.parent.mkdir(parents=True, exist_ok=True)

    def turn(total):
        usage = {"input_tokens": 3, "cache_creation_input_tokens": 1_000, "cache_read_input_tokens": total - 1_003}
        return {"type": "assistant", "message": {"role": "assistant", "usage": usage, "content": []}}

    lines = [{"type": "user", "message": {"role": "user", "content": "go"}}, turn(STARTUP_TOKENS), turn(tokens)]
    transcript.write_text("".join(json.dumps(line) + "\n" for line in lines))
    return {"agent_id": agent_id, "transcript_path": str(session)}


def via_main(monkeypatch, capsys, **payload):
    """Run the hook over a PreToolUse payload; returns the emitted hookSpecificOutput or None."""
    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps(payload)))
    delegation_hook.main()
    out = capsys.readouterr().out
    return json.loads(out)["hookSpecificOutput"] if out.strip() else None


def spawn(background, model="sonnet"):
    """A main-thread `Agent` payload — no agent_id, since the main thread never carries one."""
    tool_input = {"prompt": "read auth.py, answer whether the token is refreshed"}
    if background is not None:
        tool_input["run_in_background"] = background
    if model is not None:
        tool_input["model"] = model
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
    assert f"{CONTEXT_BUDGET // 1000}k tokens of context" in out["additionalContext"]


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


# ── subagents name a model, and it is not Fable ──────────────────────────────


@pytest.mark.parametrize("model", [None, "fable"])
def test_spawn_without_a_cheaper_model_denied(monkeypatch, capsys, model):
    """Omitted `model` inherits the Fable session; explicit `fable` is the same bill."""
    out = via_main(monkeypatch, capsys, **spawn(background=None, model=model))
    assert out["permissionDecision"] == "deny"
    for tier in ("`opus`", "`sonnet`", "`haiku`"):
        assert tier in out["permissionDecisionReason"]


@pytest.mark.parametrize("model", ["opus", "sonnet", "haiku"])
def test_spawn_with_a_named_cheaper_model_runs(monkeypatch, capsys, model):
    out = via_main(monkeypatch, capsys, **spawn(background=None, model=model))
    assert "permissionDecision" not in out


@pytest.fixture
def plugin_agent(tmp_path, monkeypatch):
    """Install a `codex` plugin whose `codex-rescue` agent definition carries `frontmatter`."""

    def install(frontmatter):
        install_path = tmp_path / "cache" / "openai-codex" / "codex" / "1.0.6"
        (install_path / "agents").mkdir(parents=True)
        (install_path / "agents" / "codex-rescue.md").write_text(
            f"---\n{frontmatter}\n---\n\nmodel: opus in the body\n"
        )
        registry = tmp_path / "installed_plugins.json"
        registry.write_text(
            json.dumps({"version": 2, "plugins": {"codex@openai-codex": [{"installPath": str(install_path)}]}})
        )
        monkeypatch.setattr(delegation_hook, "INSTALLED_PLUGINS", registry)
        payload = spawn(background=None, model=None)
        payload["tool_input"]["subagent_type"] = "codex:codex-rescue"
        return payload

    return install


def test_plugin_agent_pinned_to_a_cheaper_model_runs_without_one(monkeypatch, capsys, plugin_agent):
    """`codex:codex-rescue` pins `model: sonnet`, so omitting `model` never reaches Fable."""
    out = via_main(monkeypatch, capsys, **plugin_agent("name: codex-rescue\nmodel: sonnet\ntools: Bash"))
    assert "permissionDecision" not in out


@pytest.mark.parametrize("frontmatter", ["name: codex-rescue\nmodel: inherit", "name: codex-rescue"])
def test_plugin_agent_on_the_session_model_denied(monkeypatch, capsys, plugin_agent, frontmatter):
    """`inherit`, or no `model:` in the frontmatter (one in the body doesn't count), is the Fable session."""
    out = via_main(monkeypatch, capsys, **plugin_agent(frontmatter))
    assert out["permissionDecision"] == "deny"


def test_non_agent_tool_in_main_thread_is_silent(monkeypatch, capsys):
    assert via_main(monkeypatch, capsys, tool_name="Bash", tool_input={"command": "ls"}) is None


# ── the subagent context budget ──────────────────────────────────────────────


def test_context_below_the_warning_is_silent(monkeypatch, capsys, session):
    out = via_main(monkeypatch, capsys, tool_name="Read", tool_input={}, **at_context(session, SUBAGENT, 150_000))
    assert out is None


def test_many_cheap_calls_are_not_a_budget(monkeypatch, capsys, session):
    """60 Greps that keep the context small used to hit the 30-call cap; tokens are what counts."""
    burn(SUBAGENT, 60)
    out = via_main(monkeypatch, capsys, tool_name="Grep", tool_input={}, **at_context(session, SUBAGENT, 120_000))
    assert out is None


def test_warning_starts_at_the_threshold(monkeypatch, capsys, session):
    payload = at_context(session, SUBAGENT, CONTEXT_WARN_FROM)
    out = via_main(monkeypatch, capsys, tool_name="Read", tool_input={}, **payload)
    assert "permissionDecision" not in out
    assert f"{CONTEXT_WARN_FROM // 1000}k/{CONTEXT_BUDGET // 1000}k tokens" in out["additionalContext"]


def test_context_at_the_budget_still_runs(monkeypatch, capsys, session):
    out = via_main(
        monkeypatch, capsys, tool_name="Read", tool_input={}, **at_context(session, SUBAGENT, CONTEXT_BUDGET)
    )
    assert "permissionDecision" not in out


def test_context_past_the_budget_is_denied(monkeypatch, capsys, session):
    payload = at_context(session, SUBAGENT, CONTEXT_BUDGET + 1)
    out = via_main(monkeypatch, capsys, tool_name="Read", tool_input={}, **payload)
    assert out["permissionDecision"] == "deny"
    assert "context budget is spent" in out["permissionDecisionReason"]
    assert "Write your final message now" in out["permissionDecisionReason"]


def test_budget_is_per_agent(monkeypatch, capsys, session):
    at_context(session, SUBAGENT, CONTEXT_BUDGET + 1)
    out = via_main(monkeypatch, capsys, tool_name="Read", tool_input={}, **at_context(session, "other-agent", 90_000))
    assert out is None


def test_missing_transcript_fails_loud(monkeypatch, capsys, session):
    """A gate that quietly stops reading the context looks exactly like an agent under budget."""
    with pytest.raises(FileNotFoundError):
        via_main(monkeypatch, capsys, tool_name="Read", tool_input={}, agent_id=SUBAGENT, transcript_path=str(session))


def test_main_thread_has_no_budget(monkeypatch, capsys):
    """The main thread carries no agent_id, so it has no subagent transcript to measure."""
    assert via_main(monkeypatch, capsys, tool_name="Read", tool_input={}) is None


def test_spent_budget_denies_a_spawn_too(monkeypatch, capsys, session):
    """An out-of-budget subagent can't delegate its way out — the deny outranks the slicing rule."""
    out = via_main(monkeypatch, capsys, **at_context(session, SUBAGENT, CONTEXT_BUDGET + 1), **spawn(background=True))
    assert out["permissionDecision"] == "deny"
    assert "budget is spent" in out["permissionDecisionReason"]


def test_warning_and_slicing_rule_are_delivered_together(monkeypatch, capsys, session):
    out = via_main(monkeypatch, capsys, **at_context(session, SUBAGENT, CONTEXT_WARN_FROM), **spawn(background=True))
    assert f"{CONTEXT_WARN_FROM // 1000}k/{CONTEXT_BUDGET // 1000}k tokens" in out["additionalContext"]
    assert f"This subagent gets {CONTEXT_BUDGET // 1000}k tokens" in out["additionalContext"]


# ── the subagent wall-clock budget ───────────────────────────────────────────


def test_time_warning_starts_at_the_threshold(monkeypatch, capsys, session):
    burn(SUBAGENT, 1)
    backdate(SUBAGENT, TIME_WARN_FROM_SECONDS)
    out = via_main(monkeypatch, capsys, tool_name="Read", tool_input={}, **at_context(session, SUBAGENT, 90_000))
    assert "permissionDecision" not in out
    assert f"5.0/{TIME_BUDGET_MINUTES} minutes used" in out["additionalContext"]


def test_call_past_the_time_budget_is_denied(monkeypatch, capsys, session):
    """Few slow calls that barely grow the context — the time axis catches what tokens miss."""
    burn(SUBAGENT, 1)
    backdate(SUBAGENT, TIME_BUDGET_SECONDS + 1)
    out = via_main(monkeypatch, capsys, tool_name="Read", tool_input={}, **at_context(session, SUBAGENT, 90_000))
    assert out["permissionDecision"] == "deny"
    assert "wall-clock budget is spent" in out["permissionDecisionReason"]
    assert "Write your final message now" in out["permissionDecisionReason"]


def test_fresh_agent_is_nowhere_near_the_time_budget(monkeypatch, capsys, session):
    burn(SUBAGENT, 1)
    out = via_main(monkeypatch, capsys, tool_name="Read", tool_input={}, **at_context(session, SUBAGENT, 90_000))
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
