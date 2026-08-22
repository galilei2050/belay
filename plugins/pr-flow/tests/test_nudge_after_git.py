"""What the PostToolUse nudge says after a git command — and, more often, when it says nothing."""


def context(output):
    """The nudge text a PostToolUse output carries."""
    assert output is not None, "expected a nudge, hook stayed silent"
    return output["hookSpecificOutput"]["additionalContext"]


# ── after a commit ──────────────────────────────────────────────────────────


def test_commit_with_unpushed_work_asks_for_the_push(bash, repo, commit):
    commit(repo)
    assert "push" in context(bash("git commit -m 'x'", repo)).lower()


def test_nudge_counts_the_commits_that_are_only_local(bash, repo, commit):
    commit(repo, "one.py")
    commit(repo, "two.py")
    assert "2 commit" in context(bash("git commit -m 'x'", repo))


def test_branch_that_was_never_pushed_counts_its_whole_history(bash, repo, git, commit):
    """No upstream means no `@{u}` to count against — the fallback must still produce a number."""
    git(repo, "checkout", "-q", "-b", "fresh")
    commit(repo)
    assert "3 commit" in context(bash("git commit -m 'x'", repo))


def test_commit_on_trunk_is_silent(bash, repo, git, commit):
    git(repo, "checkout", "-q", "main")
    commit(repo)
    assert bash("git commit -m 'x'", repo) is None


def test_dry_run_commit_is_silent(bash, repo, commit):
    commit(repo)
    assert bash("git commit --dry-run", repo) is None


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


def test_a_merged_pr_counts_as_no_pr(bash, repo):
    """`gh pr view` answers with the merged PR of a reused branch; that branch still needs one."""
    assert "no open PR" in context(bash("git push", repo, gh_mode="merged"))


def test_unauthenticated_gh_stays_silent(bash, repo):
    """A `gh` that cannot answer must not be read as "this branch has no PR"."""
    assert bash("git push", repo, gh_mode="unauth") is None


def test_commit_on_a_fully_pushed_branch_with_a_pr_is_silent(bash, repo):
    """Only a push reopens the question of whether the body is stale — a commit does not."""
    assert bash("git commit -m 'x'", repo, gh_mode="open") is None
