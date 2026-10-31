"""Tests for plugins/fable-guard-hook/hooks/fable_guard_hook.py.

Decisions are driven through `main()` rather than `classify()`, so each case asserts the payload
Claude Code actually receives.
"""

import io
import json

import fable_guard_hook
import pytest

WRITE_TOOLS = ["Write", "Edit", "MultiEdit"]


def via_main(monkeypatch, capsys, tool_name, file_path, agent_id=None):
    """Run the hook over a synthesised PreToolUse payload; returns the emitted JSON or None."""
    key = "notebook_path" if tool_name == "NotebookEdit" else "file_path"
    payload: dict[str, object] = {"tool_name": tool_name, "tool_input": {key: str(file_path)}}
    if agent_id is not None:
        payload["agent_id"] = agent_id
    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps(payload)))
    fable_guard_hook.main()
    out = capsys.readouterr().out
    return json.loads(out) if out.strip() else None


# ── main-thread edits are denied ──────────────────────────────────────────────


def test_write_from_main_thread_emits_the_whole_deny_payload(monkeypatch, capsys, fix_project_dir):
    out = via_main(monkeypatch, capsys, "Write", fix_project_dir / "src" / "app.py")
    assert out["hookSpecificOutput"]["hookEventName"] == "PreToolUse"  # how the harness routes it
    assert out["hookSpecificOutput"]["permissionDecision"] == "deny"
    assert "SendMessage" in out["hookSpecificOutput"]["permissionDecisionReason"]


@pytest.mark.parametrize("tool_name", [*WRITE_TOOLS, "NotebookEdit"])
def test_every_matched_tool_is_gated(monkeypatch, capsys, fix_project_dir, tool_name):
    """The four tools in hooks.json's matcher — NotebookEdit included, which names its target
    `notebook_path` instead of `file_path`."""
    out = via_main(monkeypatch, capsys, tool_name, fix_project_dir / "src" / "notebook.ipynb")
    assert out["hookSpecificOutput"]["permissionDecision"] == "deny"


def test_reason_names_the_tool(monkeypatch, capsys, fix_project_dir):
    out = via_main(monkeypatch, capsys, "Edit", fix_project_dir / "src" / "app.py")
    assert "Edit" in out["hookSpecificOutput"]["permissionDecisionReason"]


def test_traversal_out_of_scratch_is_still_denied(monkeypatch, capsys, fix_project_dir):
    """`..` is collapsed before the exempt check, so scratch can't be used as a springboard."""
    sneaky = fix_project_dir / ".scratch" / ".." / "src" / "app.py"
    out = via_main(monkeypatch, capsys, "Write", sneaky)
    assert out["hookSpecificOutput"]["permissionDecision"] == "deny"


# ── everything else stays out of the way (emits nothing) ─────────────────────


def test_subagent_edit_is_silent(monkeypatch, capsys, fix_project_dir):
    """A subagent (`agent_id` present) editing IS the delegation this hook exists to force."""
    out = via_main(monkeypatch, capsys, "Write", fix_project_dir / "src" / "app.py", agent_id="abc123")
    assert out is None


def test_scratch_from_main_thread_is_silent(monkeypatch, capsys, fix_project_dir):
    out = via_main(monkeypatch, capsys, "Write", fix_project_dir / ".scratch" / "one_off.py")
    assert out is None


def test_out_of_project_is_silent(monkeypatch, capsys, tmp_path):
    out = via_main(monkeypatch, capsys, "Write", tmp_path / "elsewhere" / "x.py")
    assert out is None
