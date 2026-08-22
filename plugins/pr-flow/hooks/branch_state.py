#!/usr/bin/env python3
"""What the current branch still owes: a push, a PR, or a refreshed PR body.

The one place in this plugin that talks to git and `gh`. Both hook scripts ask it the same
question and differ only in what they do with the answer, so the repo is read exactly one way:
from the repository itself, never from the output of the command the agent just ran. `ahead`
and the PR list are facts; "the push probably worked" is not.

A `Step` carries facts, not sentences — `nudges.py` turns it into either wording, and
`require_pr.py` decides which kinds may block a turn.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from typing import NamedTuple, TypedDict

GIT = shutil.which("git")
GH = shutil.which("gh")

# A branch named like this is not work heading for a PR — it is the trunk itself, or a detached
# HEAD. Neither has a PR to open.
TRUNK_BRANCHES = frozenset({"main", "master", "HEAD"})

# `gh` makes a network call, and a hung one must not turn every `git commit` into a hook crash.
# Read from the environment so the timeout path can be exercised without a 15-second test.
GH_TIMEOUT_S = float(os.environ.get("PR_FLOW_GH_TIMEOUT_S", "15"))


class Step(NamedTuple):
    """What the branch still needs, and the facts each wording needs to say it.

    ``kind`` doubles as the dedupe key for the Stop backstop: the agent gets one refusal per
    (HEAD, kind), so a push turns "push" into "open" and earns a second one, while an agent
    that answers a refusal in words is not trapped in a loop.
    """

    kind: str
    branch: str
    unpushed: str = ""  # not `count`: a NamedTuple field by that name would shadow `tuple.count`
    number: int = 0
    url: str = ""


class PullRequest(TypedDict):
    """The slice of `gh pr list --json` this plugin asks for."""

    number: int
    url: str


class PrLookup(NamedTuple):
    """What `gh` could tell us about this branch's PR.

    ``answered`` is the load-bearing field: a `gh` that is missing, unauthenticated, offline or
    hung says nothing about whether a PR exists, and reporting "you have no PR" on that basis
    would send the agent off to open a duplicate.
    """

    answered: bool
    pr: PullRequest | None


def _run(binary: str, cwd: str, *args: str, timeout: float | None = None) -> subprocess.CompletedProcess[str]:
    """Run a command and capture its output; a non-zero exit is the caller's to interpret."""
    # S603: fixed argv from this module, resolved binary, no shell; only `cwd` is caller-supplied.
    return subprocess.run(  # noqa: S603
        (binary, *args), cwd=cwd, capture_output=True, text=True, check=False, timeout=timeout
    )


def git(cwd: str, *args: str) -> str | None:
    """Stdout of a read-only git command, or None if git is absent or refuses (not a repo, bad rev)."""
    if GIT is None:
        return None
    result = _run(GIT, cwd, *args)
    return result.stdout.strip() if result.returncode == 0 else None


def look_up_pr(cwd: str, branch: str) -> PrLookup:
    """Ask `gh` for the open PR of `branch`.

    `pr list` rather than `pr view`: an empty list is a machine-readable "there is none", where
    `pr view` reports both that and "I could not look" as a non-zero exit with prose on stderr.
    """
    if GH is None:
        return PrLookup(answered=False, pr=None)
    try:
        result = _run(
            GH, cwd, "pr", "list", "--head", branch, "--state", "open", "--json", "number,url", timeout=GH_TIMEOUT_S
        )
    except subprocess.TimeoutExpired:
        return PrLookup(answered=False, pr=None)  # a hung `gh` knows nothing about this branch
    if result.returncode != 0:
        return PrLookup(answered=False, pr=None)
    prs = json.loads(result.stdout)
    return PrLookup(answered=True, pr=prs[0] if prs else None)


def unpushed_commits(cwd: str) -> str | None:
    """How many commits on HEAD no remote has yet.

    `--not --remotes` rather than `@{u}..HEAD`: a branch pushed without `-u` has no upstream to
    count against, and counting its whole history instead would demand a push that already
    happened, forever.
    """
    return git(cwd, "rev-list", "--count", "HEAD", "--not", "--remotes")


def has_something_to_propose(cwd: str) -> bool:
    """True iff this branch holds commits the remote trunk does not — i.e. a PR could exist.

    A branch level with trunk (freshly cut, or already merged and still checked out) has nothing
    `gh pr create` could build a PR from, so demanding one would be a demand nothing satisfies.
    Unknown trunk (no `origin/HEAD`) means we cannot rule the branch out, so it counts as work.
    """
    ahead = git(cwd, "rev-list", "--count", "origin/HEAD..HEAD")
    return ahead != "0"


def next_step(cwd: str) -> Step | None:
    """What this branch still owes, or None if it owes nothing this hook can see."""
    branch = git(cwd, "rev-parse", "--abbrev-ref", "HEAD")
    if branch is None or branch in TRUNK_BRANCHES:
        return None
    if not git(cwd, "remote"):
        return None  # local-only repo: nothing to push to, no PR to open

    count = unpushed_commits(cwd)
    if count and count != "0":
        return Step(kind="push", branch=branch, unpushed=count)
    return pr_step(cwd, branch)


def pr_step(cwd: str, branch: str) -> Step | None:
    """What a fully-pushed branch owes: a PR, a re-read of its body, or nothing."""
    if not has_something_to_propose(cwd):
        return None
    answered, pr = look_up_pr(cwd, branch)
    if not answered:
        return None  # `gh` cannot answer — silence beats a wrong "you have no PR"
    if pr is None:
        return Step(kind="open", branch=branch)
    return Step(kind="update", branch=branch, number=pr["number"], url=pr["url"])
