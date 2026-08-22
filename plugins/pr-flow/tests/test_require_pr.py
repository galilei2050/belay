"""When the Stop backstop refuses to let the turn end, and when it must get out of the way."""


def test_blocks_while_commits_are_unpushed(stop, repo, commit):
    commit(repo)
    output = stop(repo)
    assert output["decision"] == "block"
    assert "unpushed" in output["reason"]


def test_blocks_when_a_pushed_branch_has_no_pr(stop, repo):
    output = stop(repo, gh_mode="none")
    assert output["decision"] == "block"
    assert "pr-flow:pr-description" in output["reason"]


def test_refuses_once_per_state(stop, repo, commit):
    commit(repo)
    assert stop(repo)["decision"] == "block"
    assert stop(repo) is None, "a second refusal over the same HEAD would loop the agent"


def test_refuses_again_once_the_state_moves_on(stop, repo, commit, git):
    """Pushing turns "push me" into "open a PR" — a different demand, so it gets its own refusal."""
    commit(repo)
    assert stop(repo)["decision"] == "block"
    git(repo, "push", "-q")
    assert stop(repo, gh_mode="none")["decision"] == "block"


def test_refuses_again_when_new_work_lands_on_the_same_branch(stop, repo, commit):
    """The dedupe key is (HEAD, step): more commits is a new state, not the one already refused."""
    commit(repo, "one.py")
    assert stop(repo)["decision"] == "block"
    commit(repo, "two.py")
    assert stop(repo)["decision"] == "block"


def test_silent_when_the_branch_is_pushed_and_has_a_pr(stop, repo):
    assert stop(repo, gh_mode="open") is None


def test_silent_when_gh_cannot_answer(stop, repo):
    """Blocking a turn over a PR we could not look up would strand an offline agent."""
    assert stop(repo, gh_mode="unauth") is None


def test_silent_when_the_branch_holds_nothing_trunk_lacks(stop, repo, git):
    """A branch level with trunk cannot have a PR opened for it, so demanding one traps the agent."""
    git(repo, "checkout", "-q", "-b", "scratch", "origin/HEAD")
    git(repo, "push", "-q", "-u", "origin", "scratch")
    assert stop(repo, gh_mode="none") is None


def test_silent_on_trunk(stop, repo, git, commit):
    git(repo, "checkout", "-q", "main")
    commit(repo)
    assert stop(repo) is None


def test_silent_while_another_stop_hook_is_running(stop, repo, commit):
    """`stop_hook_active` means the agent was already sent back once; blocking again loops it."""
    commit(repo)
    assert stop(repo, active=True) is None


def test_silent_without_a_remote(stop, tmp_path, git, commit):
    """A local-only repo has nothing to push to and no PR to open."""
    root = tmp_path / "local"
    root.mkdir()
    git(root, "init", "-q", "-b", "feature")
    git(root, "config", "user.email", "t@example.com")
    git(root, "config", "user.name", "t")
    commit(root)
    assert stop(root) is None


def test_silent_outside_a_repo(stop, tmp_path):
    plain = tmp_path / "plain"
    plain.mkdir()
    assert stop(plain) is None
