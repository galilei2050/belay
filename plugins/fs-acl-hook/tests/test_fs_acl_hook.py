"""Tests for plugins/fs-acl-hook/hooks/fs_acl_hook.py.

The hook is a pure function: stdin JSON → stdout JSON. Tests call `classify()` directly for
per-rule assertions, and `main()` through a synthesised stdin for the full emit path.
"""

import io
import json

import fs_acl_hook
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
