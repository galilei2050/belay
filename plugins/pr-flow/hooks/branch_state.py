"""What the current branch still owes: a push, a PR, or a refreshed PR body.

The one place in this plugin that talks to git and `gh`. Both hook scripts ask it the same
question and differ only in what they do with the answer, so the repo is read exactly one way:
from the repository itself, never from the output of the command the agent just ran. `ahead`
and the PR list are facts; "the push probably worked" is not.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from typing import NamedTuple, TypedDict

from nudges import OPEN_NUDGE, OPEN_REFUSAL, PUSH_NUDGE, PUSH_REFUSAL, UPDATE_NUDGE

GIT = shutil.which("git")
GH = shutil.which("gh")

# A branch named like this is not work heading for a PR — it is the trunk itself, or a detached
# HEAD. Neither has a PR to open.
TRUNK_BRANCHES = frozenset({"main", "master", "HEAD"})

GH_TIMEOUT_S = 15


class Step(NamedTuple):
    """What the branch still needs, and the two ways of saying so.

    ``kind`` doubles as the dedupe key for the Stop backstop: the agent gets one refusal per
    (HEAD, kind), so a push turns "push" into "open" and earns a second one, while an agent
    that answers a refusal in words is not trapped in a loop. ``refusal`` is empty for steps
    that are a judgment call rather than a hard omission.
    """

    kind: str
    nudge: str
    refusal: str


class PullRequest(TypedDict):
    """The slice of `gh pr view --json` this plugin asks for."""

    number: int
    url: str
    state: str


class PrLookup(NamedTuple):
    """What `gh` could tell us about this branch's PR.

    ``answered`` is the load-bearing field: `gh pr view` exits non-zero both for "no pull
    requests found" and for "you are not logged in", and only the first means the branch
    actually needs a PR opened.
    """

    answered: bool
    pr: PullRequest | None


def _run(binary: str, cwd: str, *args: str, timeout: float | None = None) -> subprocess.CompletedProcess[str]:
    """Run a read-only command. `binary` is resolved by `shutil.which`, `args` are literals."""
    # S603: fixed argv from this module, resolved binary, no shell; only `cwd` is caller-supplied.
    return subprocess.run(  # noqa: S603
        (binary, *args), cwd=cwd, capture_output=True, text=True, check=False, timeout=timeout
    )


def git(cwd: str, *args: str) -> str | None:
    """Stdout of a read-only git command, or None if git is absent or refuses (not a repo, no upstream)."""
    if GIT is None:
        return None
    result = _run(GIT, cwd, *args)
    return result.stdout.strip() if result.returncode == 0 else None


def look_up_pr(cwd: str) -> PrLookup:
    """Ask `gh` for the open PR of the current branch."""
    if GH is None:
        return PrLookup(answered=False, pr=None)
    result = _run(GH, cwd, "pr", "view", "--json", "number,url,state", timeout=GH_TIMEOUT_S)
    if result.returncode == 0:
        pr = json.loads(result.stdout)
        return PrLookup(answered=True, pr=pr if pr.get("state") == "OPEN" else None)
    if "no pull requests found" in result.stderr.lower():
        return PrLookup(answered=True, pr=None)
    return PrLookup(answered=False, pr=None)


def unpushed_commits(cwd: str) -> str | None:
    """How many commits exist only here — counted against the upstream, or against nothing at all."""
    upstream = git(cwd, "rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{u}")
    if upstream is None:
        return git(cwd, "rev-list", "--count", "HEAD")
    return git(cwd, "rev-list", "--count", "@{u}..HEAD")


def next_step(cwd: str, *, after_push: bool) -> Step | None:
    """What this branch still owes, or None if it owes nothing this hook can see.

    `after_push` widens the question: right after a push it is worth asking whether an existing
    PR's body still matches the branch, but that is a judgment call, so it carries no refusal.
    """
    branch = git(cwd, "rev-parse", "--abbrev-ref", "HEAD")
    if branch is None or branch in TRUNK_BRANCHES:
        return None
    if not git(cwd, "remote"):
        return None  # local-only repo: nothing to push to, no PR to open

    count = unpushed_commits(cwd)
    if count and count != "0":
        return Step(
            kind="push",
            nudge=PUSH_NUDGE.format(branch=branch, count=count),
            refusal=PUSH_REFUSAL.format(branch=branch, count=count),
        )
    return pr_step(cwd, branch, after_push=after_push)


def pr_step(cwd: str, branch: str, *, after_push: bool) -> Step | None:
    """What a fully-pushed branch owes: a PR, a re-read of its body, or nothing."""
    answered, pr = look_up_pr(cwd)
    if not answered:
        return None  # `gh` cannot answer — silence beats a wrong "you have no PR"
    if pr is None:
        return Step(kind="open", nudge=OPEN_NUDGE.format(branch=branch), refusal=OPEN_REFUSAL.format(branch=branch))
    if not after_push:
        return None
    return Step(
        kind="update",
        nudge=UPDATE_NUDGE.format(branch=branch, number=pr["number"], url=pr["url"]),
        refusal="",
    )
