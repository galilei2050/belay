"""What the PostToolUse nudge says after a git command — and, more often, when it says nothing."""


def context(output):
    """The nudge text a PostToolUse output carries."""
    assert output is not None, "expected a nudge, hook stayed silent"
    return output["hookSpecificOutput"]["additionalContext"]


# ── after a commit ──────────────────────────────────────────────────────────


def test_commit_with_unpushed_work_asks_for_the_push(bash, repo, commit):
    commit(repo)
    assert "push" in context(bash("git commit -m 'x'", repo)).lower()


def test_nudge_counts_only_what_no_remote_has(bash, repo, commit):
    commit(repo, "one.py")
    commit(repo, "two.py")
    assert "2 commit" in context(bash("git commit -m 'x'", repo))


def test_a_branch_pushed_without_upstream_tracking_is_not_asked_to_push_again(bash, repo, git):
    """`git push origin <branch>` sets no `@{u}`; counting the whole history would nag forever."""
    git(repo, "checkout", "-q", "-b", "no-tracking")
    (repo / "extra.py").write_text("q = 1\n")
    git(repo, "add", "extra.py")
    git(repo, "commit", "-qm", "extra")
    git(repo, "push", "-q", "origin", "no-tracking")
    assert "push" not in context(bash("git push origin no-tracking", repo)).lower()


def test_commit_on_trunk_is_silent(bash, repo, git, commit):
    git(repo, "checkout", "-q", "main")
    commit(repo)
    assert bash("git commit -m 'x'", repo) is None


def test_dry_run_commit_is_silent(bash, repo, commit):
    commit(repo)
    assert bash("git commit --dry-run", repo) is None


def test_a_real_commit_beside_a_dry_run_still_nudges(bash, repo, commit):
    """`--dry-run` belongs to the segment it appears in, not to the whole Bash call."""
    commit(repo)
    assert "push" in context(bash("git commit -m x && git push --dry-run", repo)).lower()


def test_a_flag_named_in_the_commit_message_is_not_a_flag(bash, repo, commit):
    commit(repo)
    assert bash("git commit -m 'stop passing --dry-run here'", repo) is not None


def test_a_commit_after_another_command_is_still_a_commit(bash, repo, commit):
    commit(repo)
    assert bash("make lint && git commit -F .scratch/msg", repo) is not None


def test_a_commit_on_its_own_line_is_still_a_commit(bash, repo, commit):
    """The repo's own habit is `git add …` then `git commit …` in one multi-line call."""
    commit(repo)
    assert bash("git add -A\ngit commit -m x", repo) is not None


def test_a_commit_in_another_repo_is_not_ours(bash, repo, commit):
    """`-C` points git elsewhere; the branch state we could report is this repo's, not that one's."""
    commit(repo)
    assert bash("git -C /somewhere/else commit -m x", repo) is None


def test_a_repo_with_no_remote_is_silent(bash, tmp_path, git, commit):
    """Nothing to push to and no PR to open."""
    root = tmp_path / "local"
    root.mkdir()
    git(root, "init", "-q", "-b", "feature")
    git(root, "config", "user.email", "t@example.com")
    git(root, "config", "user.name", "t")
    commit(root)
    assert bash("git commit -m x", root) is None


def test_outside_a_repo_is_silent(bash, tmp_path):
    plain = tmp_path / "plain"
    plain.mkdir()
    assert bash("git commit -m x", plain) is None


def test_unrelated_bash_command_is_silent(bash, repo, commit):
    commit(repo)
    assert bash("pytest -q", repo) is None


def test_non_bash_tool_is_silent(run_hook, repo, commit):
    commit(repo)
    payload = {"hook_event_name": "PostToolUse", "tool_name": "Write", "tool_input": {"file_path": "a.py"}}
    assert run_hook("nudge_after_git.py", payload, repo) is None


def test_wrong_event_is_silent(run_hook, repo, commit):
    """The two scripts do not answer each other's events, whatever ends up wired to them."""
    commit(repo)
    payload = {"hook_event_name": "Stop", "tool_name": "Bash", "tool_input": {"command": "git commit -m x"}}
    assert run_hook("nudge_after_git.py", payload, repo) is None


# ── after a push ────────────────────────────────────────────────────────────


def test_push_without_a_pr_points_at_the_skill(bash, repo):
    nudge = context(bash("git push", repo, gh_mode="none"))
    assert "pr-flow:pr-description" in nudge
    assert "no open PR" in nudge


def test_push_with_an_open_pr_asks_to_refresh_the_body(bash, repo):
    nudge = context(bash("git push", repo, gh_mode="open"))
    assert "PR #12" in nudge
    assert "gh pr edit 12" in nudge


def test_unauthenticated_gh_stays_silent(bash, repo):
    """A `gh` that cannot answer must not be read as "this branch has no PR"."""
    assert bash("git push", repo, gh_mode="unauth") is None


def test_a_hung_gh_stays_silent_instead_of_crashing_the_hook(bash, repo):
    assert bash("git push", repo, gh_mode="hang") is None


def test_a_branch_level_with_trunk_is_asked_for_nothing(bash, repo, git):
    """Freshly cut or already merged: `gh pr create` would have no commits to build a PR from."""
    git(repo, "checkout", "-q", "-b", "scratch", "origin/HEAD")
    git(repo, "push", "-q", "-u", "origin", "scratch")
    assert bash("git push", repo, gh_mode="none") is None


def test_commit_on_a_fully_pushed_branch_with_a_pr_is_silent(bash, repo):
    """Only a push can have made the PR body stale — a commit that changed nothing has not."""
    assert bash("git commit -m 'x'", repo, gh_mode="open") is None
