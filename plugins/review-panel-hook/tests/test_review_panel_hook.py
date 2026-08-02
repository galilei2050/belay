"""Tests for plugins/review-panel-hook/hooks/review_panel_hook.py.

Three layers: `is_reviewable_commit()` as a pure predicate, `review_scope_digest()` against
a real throwaway git repo, and `main()` through a synthesised stdin for the emit path.
"""

import io
import json
from pathlib import Path

import pytest
import review_panel_hook
from review_panel_hook import REVIEWERS, is_reviewable_commit, review_scope_digest


def via_main(monkeypatch, capsys, command, cwd):
    payload = json.dumps({"tool_name": "Bash", "tool_input": {"command": command}, "cwd": str(cwd)})
    monkeypatch.setattr("sys.stdin", io.StringIO(payload))
    review_panel_hook.main()
    out = capsys.readouterr().out
    return json.loads(out) if out.strip() else None


AGENTS_DIR = Path(__file__).parent.parent / "agents"


# ── the roster matches the shipped prompts ───────────────────────────────────


def test_every_reviewer_has_a_prompt():
    """A name in REVIEWERS with no agents/<name>.md would dispatch a subagent that does not exist."""
    assert {path.stem for path in AGENTS_DIR.glob("*.md")} == set(REVIEWERS)


@pytest.mark.parametrize("name", REVIEWERS)
def test_prompt_declares_its_own_name_and_stays_read_only(name):
    """Claude Code resolves the agent by its frontmatter `name`, not by the filename."""
    frontmatter = (AGENTS_DIR / f"{name}.md").read_text().split("---")[1]
    assert f"name: {name}\n" in frontmatter
    assert "disallowedTools: Write, Edit, NotebookEdit" in frontmatter


@pytest.mark.parametrize("name", REVIEWERS)
def test_prompt_demands_the_clean_verdict(name):
    """The merge step depends on a clean reviewer saying exactly this and nothing else."""
    assert "`NO FINDINGS`" in (AGENTS_DIR / f"{name}.md").read_text()


# ── which Bash commands are commits ──────────────────────────────────────────


@pytest.mark.parametrize(
    "command",
    [
        "git commit",
        'git commit -m "fix the thing"',
        "git commit -am 'wip'",
        "git -C /some/repo commit -m x",
        "make lint && git commit -F .scratch/COMMIT_MSG",
        "git commit --amend --no-edit",
    ],
)
def test_real_commits_are_reviewable(command):
    assert is_reviewable_commit(command)


@pytest.mark.parametrize(
    "command",
    [
        "git commit --dry-run",
        "git status",
        "git log --oneline",
        "git add -A",
        "git show HEAD",
        "grep -r commit .",
    ],
)
def test_non_commits_are_not_reviewable(command):
    assert not is_reviewable_commit(command)


# ── the digest describes the code under review ───────────────────────────────


def test_digest_covers_staged_content(repo):
    assert review_scope_digest(str(repo), "git commit -m x") is not None


def test_digest_changes_when_the_staged_code_changes(repo, git):
    before = review_scope_digest(str(repo), "git commit -m x")
    (repo / "staged.py").write_text("y = 3\n")
    git(repo, "add", "staged.py")
    assert review_scope_digest(str(repo), "git commit -m x") != before


def test_nothing_staged_means_nothing_to_review(repo, git):
    git(repo, "reset", "-q")
    assert review_scope_digest(str(repo), "git commit -m x") is None


def test_commit_all_reads_the_worktree_not_the_index(repo, git):
    """`-a` stages at commit time, so an unstaged edit is still in scope for `git commit -am`."""
    git(repo, "reset", "-q")
    (repo / "base.py").write_text("x = 99\n")
    assert review_scope_digest(str(repo), "git commit -m x") is None
    assert review_scope_digest(str(repo), "git commit -am x") is not None


# ── the emit path ────────────────────────────────────────────────────────────


def test_commit_gets_the_roster(monkeypatch, capsys, repo):
    emitted = via_main(monkeypatch, capsys, 'git commit -m "add feature"', repo)
    context = emitted["hookSpecificOutput"]["additionalContext"]
    assert emitted["hookSpecificOutput"]["hookEventName"] == "PreToolUse"
    for name in REVIEWERS:
        assert f"review-panel-hook:{name}" in context
    assert "git show HEAD" in context


def test_roster_carries_no_permission_decision(monkeypatch, capsys, repo):
    """Advisory only: deciding here would bypass the permission flow and acl-hook."""
    emitted = via_main(monkeypatch, capsys, "git commit -m x", repo)
    assert "permissionDecision" not in emitted["hookSpecificOutput"]


def test_same_content_is_not_reviewed_twice(monkeypatch, capsys, repo):
    """A commit rejected by pre-commit and retried must not re-dispatch the panel."""
    assert via_main(monkeypatch, capsys, "git commit -m x", repo) is not None
    assert via_main(monkeypatch, capsys, "git commit -m x", repo) is None


def test_new_content_is_reviewed_again(monkeypatch, capsys, repo, git):
    via_main(monkeypatch, capsys, "git commit -m x", repo)
    (repo / "staged.py").write_text("y = 4\n")
    git(repo, "add", "staged.py")
    assert via_main(monkeypatch, capsys, "git commit -m x", repo) is not None


def test_non_bash_tool_is_ignored(monkeypatch, capsys, repo):
    payload = json.dumps({"tool_name": "Write", "tool_input": {"file_path": "x"}, "cwd": str(repo)})
    monkeypatch.setattr("sys.stdin", io.StringIO(payload))
    review_panel_hook.main()
    assert capsys.readouterr().out == ""


def test_dry_run_gets_nothing(monkeypatch, capsys, repo):
    assert via_main(monkeypatch, capsys, "git commit --dry-run", repo) is None


def test_outside_a_repo_gets_nothing(monkeypatch, capsys, tmp_path):
    assert via_main(monkeypatch, capsys, "git commit -m x", tmp_path) is None
