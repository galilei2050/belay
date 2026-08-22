"""Behavioral tests for review-panel.

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

_ROSTER_RE = re.compile(r"`review-panel:([\w-]+)`")


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
        # A newline separates two commands exactly like `;` does, and the agent writes them
        # this way: 3 of the 29 multi-line Bash calls in one real session commit on a later line.
        "git add -A\ngit commit -m x",
        "make lint\nmake test\ngit commit -m x",
        # The flag is prose here, not a flag — the commit is real.
        'git commit -m "stop passing --dry-run to this"',
        # `--dry-run` belongs to the segment it was written on, not to the commit beside it.
        "git commit -m x && git push --dry-run",
        "git commit --dry-run && git commit -m x",
        # git's own `-C` names another repo; `commit`'s `-C` reuses a message and commits here.
        "git commit -C HEAD~1",
        "git -C /elsewhere commit -m x && git commit -m y",
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
        # Another repository's commit: the diff this hook can measure is the session's own,
        # so reviewing it against that commit would be reviewing the wrong code.
        "git -C /somewhere/else commit -m x",
        "git --git-dir=/elsewhere/.git commit -m x",
        # The command is quoted prose, not a command.
        "echo 'run git commit next'",
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


def test_commit_all_is_reviewed_from_the_worktree(run_hook, repo, git, big_file):
    """`-a` stages at commit time, so an unstaged edit is still in scope for `git commit -am`."""
    git(repo, "reset", "-q")
    (repo / "base.py").write_text(big_file("w"))
    assert run_hook("git commit -m x", repo) is None
    assert run_hook("git commit -am x", repo) is not None


def test_a_flag_on_an_earlier_command_does_not_widen_the_review_scope(run_hook, repo, git, big_file):
    """`ls -la` carries an `-a`; reading it as `git commit -a` would review unstaged noise."""
    git(repo, "reset", "-q")
    (repo / "base.py").write_text(big_file("w"))
    assert run_hook("ls -la && git commit -m x", repo) is None


# ── a commit too small to be worth eight subagents ───────────────────────────


def test_a_two_line_commit_is_left_alone(run_hook, repo, git):
    """The round the user refused to pay for: eight subagents over a changed constant."""
    git(repo, "reset", "-q")
    (repo / "base.py").write_text("x = 2\n")
    git(repo, "add", "base.py")
    assert run_hook("git commit -m x", repo) is None


@pytest.mark.parametrize(("added", "dispatched"), [(63, False), (64, True)])
def test_the_panel_starts_at_sixty_four_changed_lines(run_hook, repo, git, added, dispatched):
    """Added and removed lines both count; the file headers do not."""
    git(repo, "reset", "-q")
    (repo / "new.py").write_text("".join(f"z{n} = {n}\n" for n in range(added)))
    git(repo, "add", "new.py")
    assert (run_hook("git commit -m x", repo) is not None) is dispatched


# ── what the agent is told ───────────────────────────────────────────────────


def test_the_panel_is_pointed_at_the_commit_that_just_landed(run_hook, repo):
    """The nudge arrives after the commit, so the index is gone and HEAD is the scope."""
    context = run_hook("git commit -m x", repo)["hookSpecificOutput"]["additionalContext"]
    assert "git show HEAD" in context


def test_the_agent_is_told_to_spend_a_round_only_on_unseen_changes(run_hook, repo):
    """A fix commit is new content, so only this instruction stops a second round of eight."""
    context = run_hook("git commit -m x", repo)["hookSpecificOutput"]["additionalContext"]
    assert "changes the panel has not seen" in context
    assert "dispatch nobody" in context


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


def test_committing_new_code_dispatches_the_panel_again(run_hook, repo, git, big_file):
    run_hook("git commit -m x", repo)
    (repo / "more.py").write_text(big_file("v"))
    git(repo, "add", "more.py")
    assert run_hook("git commit -m x", repo) is not None
