"""Behavioral tests for review-panel-hook.

Every test drives the hook through its real boundary — the script Claude Code runs, JSON on
stdin, JSON on stdout — and asserts what the agent would actually receive. Nothing imports
the hook module or calls its functions: the internals are free to be refactored, and the
tests still fail if the behavior changes.
"""

import re
from pathlib import Path

import pytest

AGENTS_DIR = Path(__file__).parent.parent / "agents"
PROMPTS = sorted(AGENTS_DIR.glob("*.md"))

_ROSTER_RE = re.compile(r"`review-panel-hook:([\w-]+)`")


def roster_of(emitted) -> set[str]:
    """The reviewer names the agent is told to dispatch."""
    return set(_ROSTER_RE.findall(emitted["hookSpecificOutput"]["additionalContext"]))


# ── the roster the agent receives matches the prompts that ship ──────────────


def test_agent_is_sent_to_every_shipped_reviewer_and_no_other(run_hook, repo):
    """A name in the roster with no prompt would dispatch a subagent that does not exist."""
    assert roster_of(run_hook("git commit -m x", repo)) == {path.stem for path in PROMPTS}


@pytest.mark.parametrize("prompt", PROMPTS, ids=lambda p: p.stem)
def test_prompt_declares_its_own_name_and_stays_read_only(prompt):
    """Claude Code resolves the agent by its frontmatter `name`, not by the filename."""
    frontmatter = prompt.read_text().split("---")[1]
    assert f"name: {prompt.stem}\n" in frontmatter
    assert "disallowedTools: Write, Edit, NotebookEdit" in frontmatter


@pytest.mark.parametrize("prompt", PROMPTS, ids=lambda p: p.stem)
def test_prompt_demands_the_clean_verdict(prompt):
    """The merge step depends on a clean reviewer saying exactly this and nothing else."""
    assert "`NO FINDINGS`" in prompt.read_text()


# ── when the panel is dispatched ─────────────────────────────────────────────


@pytest.mark.parametrize(
    "command",
    [
        "git commit",
        'git commit -m "fix the thing"',
        "git commit -am 'wip'",
        "git commit --amend --no-edit",
        "make lint && git commit -F .scratch/COMMIT_MSG",
    ],
)
def test_a_commit_puts_the_panel_on_the_agents_desk(run_hook, repo, command):
    assert run_hook(command, repo) is not None


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
def test_anything_that_does_not_create_a_commit_is_left_alone(run_hook, repo, command):
    assert run_hook(command, repo) is None


def test_a_non_bash_tool_is_left_alone(run_hook, repo):
    assert run_hook("git commit -m x", repo, tool_name="Write") is None


def test_a_commit_outside_a_repo_is_left_alone(run_hook, tmp_path):
    assert run_hook("git commit -m x", tmp_path) is None


def test_a_commit_with_nothing_staged_is_left_alone(run_hook, repo, git):
    git(repo, "reset", "-q")
    assert run_hook("git commit -m x", repo) is None


def test_commit_all_is_reviewed_from_the_worktree(run_hook, repo, git):
    """`-a` stages at commit time, so an unstaged edit is still in scope for `git commit -am`."""
    git(repo, "reset", "-q")
    (repo / "base.py").write_text("x = 99\n")
    assert run_hook("git commit -m x", repo) is None
    assert run_hook("git commit -am x", repo) is not None


# ── what the agent is told ───────────────────────────────────────────────────


def test_the_panel_is_pointed_at_the_commit_that_just_landed(run_hook, repo):
    """The nudge arrives after the commit, so the index is gone and HEAD is the scope."""
    context = run_hook("git commit -m x", repo)["hookSpecificOutput"]["additionalContext"]
    assert "git show HEAD" in context


def test_the_commit_is_neither_blocked_nor_auto_approved(run_hook, repo):
    """Advisory only: deciding here would bypass the permission flow and acl-hook."""
    emitted = run_hook("git commit -m x", repo)["hookSpecificOutput"]
    assert emitted["hookEventName"] == "PreToolUse"
    assert "permissionDecision" not in emitted


# ── the same code is never reviewed twice ────────────────────────────────────


def test_retrying_the_same_commit_does_not_re_dispatch_the_panel(run_hook, repo):
    """`pre-commit` rejects a commit, the agent retries — eight subagents must not run again."""
    assert run_hook("git commit -m x", repo) is not None
    assert run_hook("git commit -m x", repo) is None


def test_committing_new_code_dispatches_the_panel_again(run_hook, repo, git):
    run_hook("git commit -m x", repo)
    (repo / "staged.py").write_text("y = 4\n")
    git(repo, "add", "staged.py")
    assert run_hook("git commit -m x", repo) is not None
