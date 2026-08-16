"""Tests for plugins/branch-guard-hook/hooks/branch_guard_hook.py.

The hook is a pure function: stdin JSON → stdout JSON. Tests call `classify()` / `current_branch()`
directly for per-rule assertions, and `main()` through a synthesised stdin for the full emit path.
"""

import io
import json

import branch_guard_hook
from branch_guard_hook import Decision, classify, current_branch


def denied(tool_name, file_path, branch) -> Decision:
    """classify() narrowed to a real decision — for the cases that must deny, not stay silent."""
    result = classify(tool_name, file_path, branch)
    assert result is not None
    return result


def via_main(monkeypatch, capsys, tool_name, file_path, cwd):
    payload = json.dumps({"tool_name": tool_name, "tool_input": {"file_path": file_path}, "cwd": str(cwd)})
    monkeypatch.setattr("sys.stdin", io.StringIO(payload))
    branch_guard_hook.main()
    out = capsys.readouterr().out
    return json.loads(out) if out.strip() else None


# ── edits on trunk are denied ────────────────────────────────────────────────


def test_write_on_main_is_denied(fix_project_dir):
    decision, reason = denied("Write", str(fix_project_dir / "src" / "app.py"), "main")
    assert decision == "deny"
    assert "git checkout -b" in reason


def test_edit_on_master_is_denied(fix_project_dir):
    assert denied("Edit", str(fix_project_dir / "src" / "app.py"), "master")[0] == "deny"


def test_traversal_out_of_scratch_is_still_denied(fix_project_dir):
    """`..` is collapsed before the exempt-dir check, so scratch can't be used as a springboard."""
    sneaky = str(fix_project_dir / ".scratch" / ".." / "src" / "app.py")
    assert denied("Write", sneaky, "main")[0] == "deny"


# ── everything else stays out of the way (emits nothing) ─────────────────────


def test_feature_branch_defers(fix_project_dir):
    assert classify("Write", str(fix_project_dir / "src" / "app.py"), "feature-x") is None


def test_scratch_on_main_defers(fix_project_dir):
    assert classify("Write", str(fix_project_dir / ".scratch" / "notes.md"), "main") is None


def test_claude_dir_on_main_defers(fix_project_dir):
    assert classify("Edit", str(fix_project_dir / ".claude" / "settings.local.json"), "main") is None


def test_out_of_project_defers(tmp_path):
    assert classify("Write", str(tmp_path / "elsewhere" / "x.py"), "main") is None


def test_read_defers(fix_project_dir):
    assert classify("Read", str(fix_project_dir / "src" / "app.py"), "main") is None


def test_no_branch_defers(fix_project_dir):
    assert classify("Write", str(fix_project_dir / "src" / "app.py"), None) is None


# ── branch detection ─────────────────────────────────────────────────────────


def test_current_branch_reads_head(on_branch):
    project = on_branch("feature-x")
    assert current_branch(str(project / "src")) == "feature-x"


def test_detached_head_has_no_branch(fix_project_dir):
    (fix_project_dir / ".git" / "HEAD").write_text("9f641e0c" * 5 + "\n", encoding="utf-8")
    assert current_branch(str(fix_project_dir)) is None


def test_outside_a_checkout_has_no_branch(tmp_path):
    assert current_branch(str(tmp_path)) is None


def test_worktree_gitdir_file_is_followed(fix_project_dir, tmp_path):
    """A linked worktree's `.git` is a file pointing at its own dir — with its own HEAD."""
    worktree = tmp_path / "wt"
    worktree.mkdir()
    gitdir = fix_project_dir / ".git" / "worktrees" / "wt"
    gitdir.mkdir(parents=True)
    (gitdir / "HEAD").write_text("ref: refs/heads/feature-x\n", encoding="utf-8")
    (worktree / ".git").write_text(f"gitdir: {gitdir}\n", encoding="utf-8")
    assert current_branch(str(worktree)) == "feature-x"


# ── emit path ────────────────────────────────────────────────────────────────


def test_main_emits_deny_json(monkeypatch, capsys, fix_project_dir):
    out = via_main(monkeypatch, capsys, "Write", str(fix_project_dir / "src" / "app.py"), fix_project_dir)
    assert out["hookSpecificOutput"]["permissionDecision"] == "deny"


def test_main_stays_silent_on_a_feature_branch(monkeypatch, capsys, on_branch):
    project = on_branch("feature-x")
    assert via_main(monkeypatch, capsys, "Write", str(project / "src" / "app.py"), project) is None
