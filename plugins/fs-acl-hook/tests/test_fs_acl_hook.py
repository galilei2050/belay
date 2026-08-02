"""Tests for plugins/fs-acl-hook/hooks/fs_acl_hook.py.

The hook is a pure function: stdin JSON → stdout JSON. Tests call `classify()` directly for
per-rule assertions, and `main()` through a synthesised stdin for the full emit path.
"""

import io
import json
from pathlib import Path

import fs_acl_hook
import pytest
from fs_acl_hook import Decision, classify


def decided(tool_name, file_path) -> Decision:
    """classify() narrowed to a real decision — for tests that expect allow/ask/deny, not a defer."""
    result = classify(tool_name, file_path)
    assert result is not None
    return result


def via_main(monkeypatch, capsys, tool_name, file_path):
    payload = json.dumps({"tool_name": tool_name, "tool_input": {"file_path": file_path}})
    monkeypatch.setattr("sys.stdin", io.StringIO(payload))
    fs_acl_hook.main()
    out = capsys.readouterr().out
    return json.loads(out) if out.strip() else None


# ── .git is off-limits (read AND write) ──────────────────────────────────────


def test_write_into_git_is_denied(fix_project_dir):
    decision, reason = decided("Write", str(fix_project_dir / ".git" / "COMMIT_MSG_TMP"))
    assert decision == "deny"
    assert ".scratch" in reason


def test_read_git_dir_is_denied(fix_project_dir):
    assert decided("Read", str(fix_project_dir / ".git" / "config"))[0] == "deny"


# ── scratch writes are allowed (prompt suppressed) ───────────────────────────


def test_write_under_scratch_is_allowed(fix_project_dir):
    assert decided("Write", str(fix_project_dir / ".scratch" / "COMMIT_MSG"))[0] == "allow"


def test_edit_under_scratch_is_allowed(fix_project_dir):
    assert decided("Edit", str(fix_project_dir / ".scratch" / "notes.txt"))[0] == "allow"


# ── in-project source edits defer to the normal flow ─────────────────────────


def test_write_in_project_source_defers(fix_project_dir):
    assert classify("Write", str(fix_project_dir / "src" / "app.py")) is None


def test_read_in_project_defers(fix_project_dir):
    assert classify("Read", str(fix_project_dir / "src" / "app.py")) is None


# ── writes outside the project are denied ────────────────────────────────────


def test_write_to_tmp_is_denied():
    decision, reason = decided("Write", "/tmp/scratch-file.txt")
    assert decision == "deny"
    assert ".scratch" in reason


def test_edit_sibling_repo_via_traversal_is_denied(fix_project_dir):
    # The Edit(../other/project/file.py) case: cd into that repo instead.
    decision, reason = decided("Edit", str(fix_project_dir / ".." / "other" / "file.py"))
    assert decision == "deny"
    assert "cd into" in reason


# ── reads outside the project ask (guardrail + escape) ───────────────────────


def test_read_outside_project_asks(fix_project_dir):
    decision, reason = decided("Read", str(fix_project_dir / ".." / "baski" / "core.py"))
    assert decision == "ask"
    assert "cross-repo" in reason


# ── ~/.claude is the agent's own home ────────────────────────────────────────
#
# Driven through main() rather than classify(), so each case asserts the decision Claude Code
# actually receives.

CLAUDE_HOME = Path.home() / ".claude"


def decision_for(monkeypatch, capsys, tool_name, path):
    out = via_main(monkeypatch, capsys, tool_name, str(path))
    assert out is not None, f"{tool_name} {path} deferred instead of deciding"
    return out["hookSpecificOutput"]["permissionDecision"]


@pytest.mark.parametrize(
    "path",
    [
        CLAUDE_HOME / "projects" / "-home-galilei-Projects-belay" / "memory" / "MEMORY.md",
        CLAUDE_HOME / "projects" / "-x" / "memory" / "feedback_thing.md",
        CLAUDE_HOME / "logs" / "acl-hook.log",
        CLAUDE_HOME / "plans" / "draft.md",
    ],
)
def test_writing_the_agents_own_home_is_allowed(monkeypatch, capsys, path):
    assert decision_for(monkeypatch, capsys, "Write", path) == "allow"


def test_reading_the_agents_own_home_does_not_prompt(monkeypatch, capsys):
    path = CLAUDE_HOME / "projects" / "-x" / "memory" / "MEMORY.md"
    assert decision_for(monkeypatch, capsys, "Read", path) == "allow"


@pytest.mark.parametrize("name", [".credentials.json", ".env"])
@pytest.mark.parametrize("tool_name", ["Read", "Write"])
def test_secrets_under_claude_home_are_off_limits_both_ways(monkeypatch, capsys, tool_name, name):
    decision = decision_for(monkeypatch, capsys, tool_name, CLAUDE_HOME / name)
    assert decision == "deny"


@pytest.mark.parametrize("name", ["settings.json", "settings.local.json", "acl.json"])
def test_writing_the_files_that_grant_permission_needs_the_user(monkeypatch, capsys, name):
    """The agent must not widen its own leash without the user seeing it."""
    assert decision_for(monkeypatch, capsys, "Write", CLAUDE_HOME / name) == "ask"


@pytest.mark.parametrize("name", ["settings.json", "acl.json"])
def test_reading_those_same_files_is_routine(monkeypatch, capsys, name):
    """The update-config skill reads settings before changing anything."""
    assert decision_for(monkeypatch, capsys, "Read", CLAUDE_HOME / name) == "allow"


def test_a_project_dot_claude_is_not_the_agents_home(monkeypatch, capsys, fix_project_dir):
    """`<project>/.claude` is ordinary project config — it must not inherit the home carve-out."""
    out = via_main(monkeypatch, capsys, "Read", str(fix_project_dir / ".claude" / "settings.json"))
    assert out is None


# ── full emit path via main() ────────────────────────────────────────────────


def test_main_emits_allow_for_scratch(monkeypatch, capsys, fix_project_dir):
    out = via_main(monkeypatch, capsys, "Write", str(fix_project_dir / ".scratch" / "x"))
    assert out is not None
    assert out["hookSpecificOutput"]["permissionDecision"] == "allow"


def test_main_emits_deny_for_git(monkeypatch, capsys, fix_project_dir):
    out = via_main(monkeypatch, capsys, "Write", str(fix_project_dir / ".git" / "x"))
    assert out is not None
    assert out["hookSpecificOutput"]["permissionDecision"] == "deny"


def test_main_emits_nothing_for_in_project_read(monkeypatch, capsys, fix_project_dir):
    out = via_main(monkeypatch, capsys, "Read", str(fix_project_dir / "src" / "app.py"))
    assert out is None


def test_main_ignores_other_tools(monkeypatch, capsys):
    payload = json.dumps({"tool_name": "Bash", "tool_input": {"command": "ls"}})
    monkeypatch.setattr("sys.stdin", io.StringIO(payload))
    fs_acl_hook.main()
    assert capsys.readouterr().out.strip() == ""
