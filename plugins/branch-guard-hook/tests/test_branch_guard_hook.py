"""Tests for plugins/branch-guard-hook/hooks/branch_guard_hook.py.

Decisions are driven through `main()` rather than `classify()`, so each case asserts the payload
Claude Code actually receives — and exercises `.git/HEAD` → `current_branch()` → `classify()` →
`_emit()` as one path instead of testing each half against a stub of the other. `current_branch()`
keeps a few direct tests for the checkout shapes that are awkward to reach through a payload.
"""

import io
import json

import branch_guard_hook
import pytest
from branch_guard_hook import current_branch

WRITE_TOOLS = ["Write", "Edit", "MultiEdit"]


def via_main(monkeypatch, capsys, tool_name, file_path):
    """Run the hook over a synthesised PreToolUse payload; returns the emitted JSON or None."""
    key = "notebook_path" if tool_name == "NotebookEdit" else "file_path"
    payload = json.dumps({"tool_name": tool_name, "tool_input": {key: str(file_path)}})
    monkeypatch.setattr("sys.stdin", io.StringIO(payload))
    branch_guard_hook.main()
    out = capsys.readouterr().out
    return json.loads(out) if out.strip() else None


def make_worktree(project, name, branch):
    """A linked worktree of `project` at `.claude/worktrees/<name>`, checked out on `branch`."""
    gitdir = project / ".git" / "worktrees" / name
    gitdir.mkdir(parents=True)
    (gitdir / "HEAD").write_text(f"ref: refs/heads/{branch}\n", encoding="utf-8")
    tree = project / ".claude" / "worktrees" / name
    (tree / "src").mkdir(parents=True)
    (tree / ".git").write_text(f"gitdir: {gitdir}\n", encoding="utf-8")
    return tree


# ── edits on trunk are denied ────────────────────────────────────────────────


def test_write_on_main_emits_the_whole_deny_payload(monkeypatch, capsys, fix_project_dir):
    out = via_main(monkeypatch, capsys, "Write", fix_project_dir / "src" / "app.py")
    assert out["hookSpecificOutput"]["hookEventName"] == "PreToolUse"  # how the harness routes it
    assert out["hookSpecificOutput"]["permissionDecision"] == "deny"
    assert "git checkout -b" in out["hookSpecificOutput"]["permissionDecisionReason"]


@pytest.mark.parametrize("tool_name", [*WRITE_TOOLS, "NotebookEdit"])
def test_every_matched_tool_is_gated(monkeypatch, capsys, fix_project_dir, tool_name):
    """The four tools in hooks.json's matcher — NotebookEdit included, which names its target
    `notebook_path` instead of `file_path`."""
    out = via_main(monkeypatch, capsys, tool_name, fix_project_dir / "src" / "notebook.ipynb")
    assert out["hookSpecificOutput"]["permissionDecision"] == "deny"


def test_reason_names_the_branch_and_the_tool(monkeypatch, capsys, on_branch):
    project = on_branch("master")
    out = via_main(monkeypatch, capsys, "Edit", project / "src" / "app.py")
    reason = out["hookSpecificOutput"]["permissionDecisionReason"]
    assert "`master`" in reason
    assert "Edit" in reason


def test_project_claude_dir_is_not_exempt(monkeypatch, capsys, fix_project_dir):
    """`<project>/.claude` holds tracked source (skills), so trunk edits there are PR work too."""
    skill = fix_project_dir / ".claude" / "skills" / "new-plugin"
    skill.mkdir(parents=True)
    out = via_main(monkeypatch, capsys, "Write", skill / "SKILL.md")
    assert out["hookSpecificOutput"]["permissionDecision"] == "deny"


def test_traversal_out_of_scratch_is_still_denied(monkeypatch, capsys, fix_project_dir):
    """`..` is collapsed before the exempt check, so scratch can't be used as a springboard."""
    sneaky = fix_project_dir / ".scratch" / ".." / "src" / "app.py"
    assert via_main(monkeypatch, capsys, "Write", sneaky)["hookSpecificOutput"]["permissionDecision"] == "deny"


def test_worktree_on_trunk_is_denied(monkeypatch, capsys, on_branch):
    """A worktree sitting on trunk is judged by its own HEAD, not by the main checkout's."""
    project = on_branch("dev")  # git forbids the same branch in two checkouts
    tree = make_worktree(project, "wt", "main")
    out = via_main(monkeypatch, capsys, "Write", tree / "src" / "app.py")
    assert out["hookSpecificOutput"]["permissionDecision"] == "deny"


# ── everything else stays out of the way (emits nothing) ─────────────────────


def test_feature_branch_is_silent(monkeypatch, capsys, on_branch):
    project = on_branch("feature-x")
    assert via_main(monkeypatch, capsys, "Write", project / "src" / "app.py") is None


def test_scratch_on_trunk_is_silent(monkeypatch, capsys, fix_project_dir):
    assert via_main(monkeypatch, capsys, "Write", fix_project_dir / ".scratch" / "notes.md") is None


def test_out_of_project_is_silent(monkeypatch, capsys, tmp_path):
    assert via_main(monkeypatch, capsys, "Write", tmp_path / "elsewhere" / "x.py") is None


def test_detached_head_is_silent(monkeypatch, capsys, fix_project_dir):
    (fix_project_dir / ".git" / "HEAD").write_text("9f641e0c" * 5 + "\n", encoding="utf-8")
    assert via_main(monkeypatch, capsys, "Write", fix_project_dir / "src" / "app.py") is None


def test_worktree_on_a_feature_branch_is_silent(monkeypatch, capsys, fix_project_dir):
    """The main checkout is on `main`, but the file belongs to a worktree that is not."""
    tree = make_worktree(fix_project_dir, "wt", "feature-x")
    assert via_main(monkeypatch, capsys, "Write", tree / "src" / "app.py") is None


# ── branch detection ─────────────────────────────────────────────────────────


def test_relative_gitdir_pointer_is_resolved(fix_project_dir, tmp_path):
    """Submodules and `--relative-paths` worktrees write a pointer relative to the `.git` file."""
    gitdir = fix_project_dir / ".git" / "worktrees" / "wt"
    gitdir.mkdir(parents=True)
    (gitdir / "HEAD").write_text("ref: refs/heads/feature-x\n", encoding="utf-8")
    tree = tmp_path / "wt"  # sibling of the `project/` checkout the fixture builds
    tree.mkdir()
    (tree / ".git").write_text("gitdir: ../project/.git/worktrees/wt\n", encoding="utf-8")
    assert current_branch(tree) == "feature-x"


def test_outside_a_checkout_has_no_branch(tmp_path):
    assert current_branch(tmp_path) is None


def test_unreadable_head_fails_loud(fix_project_dir):
    """A git dir whose HEAD can't be read must raise — a silent None would disable the guard."""
    (fix_project_dir / ".git" / "HEAD").unlink()
    with pytest.raises(OSError, match="HEAD"):
        current_branch(fix_project_dir / "src")
