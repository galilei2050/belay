#!/usr/bin/env python3
"""CI for the current branch's PR: one line of state, a bounded wait, and only the failing log.

The agent already has `gh`. What it does not have is a shape of `gh` that fits a context window:
`gh pr checks --watch` redraws a table on every refresh, `--log-failed` needs a run id that only
exists inside a check's URL, and a raw job log is thousands of lines whose last forty are the
failure. Left to improvise, an agent either polls `gh pr checks` in a wait loop or pastes a whole
log into its context — and usually skips the step entirely and calls a branch done on a green
push.

So: six verbs, each ending in a statement the agent can act on.

* `status` — every check, one line each, plus a verdict. One `gh` call.
* `wait`   — blocks in `gh`'s own watch until the run concludes (hard cap: `--timeout`), then
             prints the verdict and, when red, the failing log. Not a poll loop: one call that
             returns when there is something to say.
* `logs`   — the failing steps only, tail-trimmed, per run.
* `merged` — blocks until the PR leaves OPEN, then says what shipped and what to watch next.
* `deploy` — blocks on everything the merge commit set off — GitHub Actions runs and Cloud Build
             builds alike — judged and log-trimmed exactly like the PR's checks.
* `ship`   — `wait`, `merged` and `deploy` in order, in one process.

The last three exist because green CI is where an agent stops, and a green branch is not a shipped
change. The chain is CI → merge → deploy → the service's own metrics, and each verb ends by
naming the next link so the agent does not mistake a passing signal for a working system.

Every waiting verb is meant to be launched in the background — Claude Code's Bash
`run_in_background: true` — and `ship` exists for exactly that. A branch takes tens of minutes to
reach production and the session must not be frozen for any of them: one backgrounded `ship`
after a push carries the branch from a pushed commit to a live change and re-invokes the agent
once, with the whole story. A foreground wait buys nothing, and a hand-rolled `sleep` loop around
`status` costs the user the conversation as well as the API quota.

Exit codes are the summary, so a caller can branch without parsing prose: 0 green, 1 red,
2 still pending, 3 nothing to report on (no PR, no checks, no `gh`).
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import time
from typing import NamedTuple

GH = shutil.which("gh")
# Optional, unlike `gh`: a repo whose deploy runs in Actions never needs it, so its absence is an
# empty string to be checked rather than a reason to exit — and a string keeps the argv it goes
# into typed without a guard for a call that cannot happen.
GCLOUD = shutil.which("gcloud") or ""

# Long enough for a normal run to finish inside one call, short enough that a queue stuck behind a
# busy runner ends as a report rather than an agent that never comes back.
DEFAULT_WAIT_S = 900
# `gh` returning nothing within this is broken, not slow — the watch has its own budget above.
GH_TIMEOUT_S = 60
# A failed step's log is read from the bottom: the traceback and the assertion are the last lines.
DEFAULT_LOG_LINES = 60
# `gh`'s own default; low values just burn API quota against a run that takes minutes.
WATCH_INTERVAL_S = 15
# GitHub offers no watch for a merge, so `merged` is the one verb that really polls. Five minutes
# is a reviewer's clock, not a machine's: the merge is a human decision, and a tighter loop only
# spends API quota on an answer that changes once.
MERGE_POLL_S = 300
# An hour of waiting on a review, then a report. A PR nobody merged in an hour needs a person
# pinged, not an agent still sitting on it.
DEFAULT_MERGE_WAIT_S = 3600
# A deploy workflow that has not concluded in this long is an incident, not a slow build.
DEFAULT_DEPLOY_WAIT_S = 1800
# A trigger takes seconds to turn a merge into a build, and `deploy` normally runs the instant
# `merged` returns. Without a grace window the usual case — asking before the build exists — would
# report the commit as deployed by nothing at all. Read from the environment so the window can be
# exercised without a two-minute test, the way `branch_state.py` takes its `gh` timeout.
BUILD_APPEAR_S = float(os.environ.get("PR_FLOW_BUILD_APPEAR_S", "120"))

# The run id lives only in the check's URL: .../actions/runs/<run>/job/<job>. A check from outside
# Actions (a status posted by an external service) has no run and no log to fetch.
_RUN_ID_RE = re.compile(r"/actions/runs/(\d+)")

# Worst first — the failing check is what the reader came for, and a long list scrolls the top away.
_BUCKET_ORDER = {"fail": 0, "pending": 1, "cancel": 2, "skipping": 3, "pass": 4}

# `gh run list` reports a conclusion where `gh pr checks` reports a bucket. Mapping one onto the
# other lets a deploy run be judged, sorted and log-trimmed by the code that already does it for
# checks. An empty conclusion means the run has not finished — that is `pending`, never `pass`.
_RUN_CONCLUSIONS = {
    "success": "pass",
    "neutral": "pass",
    "failure": "fail",
    "timed_out": "fail",
    "startup_failure": "fail",
    "action_required": "fail",
    "cancelled": "cancel",
    "skipped": "skipping",
}

# The same mapping for Cloud Build, whose builds are judged by the code that judges checks. An
# unlisted status falls through to `pending` — a deploy this script cannot name is one it keeps
# waiting on, never one it calls green.
_BUILD_STATUSES = {
    "SUCCESS": "pass",
    "FAILURE": "fail",
    "INTERNAL_ERROR": "fail",
    "TIMEOUT": "fail",
    "EXPIRED": "fail",
    "CANCELLED": "cancel",
    "QUEUED": "pending",
    "WORKING": "pending",
    "PENDING": "pending",
    "STATUS_UNKNOWN": "pending",
}

EXIT_GREEN, EXIT_RED, EXIT_PENDING, EXIT_UNKNOWN = 0, 1, 2, 3

# The verbs that block, as opposed to `status` and `logs`, which answer in one call. Named here
# because `hooks/require_background.py` denies exactly these in the foreground, and a fifth
# waiting verb added without the hook knowing would be a wait the gate lets through.
BLOCKING_VERBS = ("wait", "merged", "deploy", "ship")

# How this script was invoked, so every "run this next" sentence is a line the agent can paste.
ME = f"python3 {sys.argv[0]}"


class Check(NamedTuple):
    """One thing that ran and can pass or fail: a PR check, an Actions run, a Cloud Build build.

    Named for the shape `gh pr checks --json` reports, because that is the majority case and the
    display, the sort and the verdict are all written against it. A deploy is judged by the same
    code, so a Cloud Build build arrives here too and carries its id in `build` — the one field
    that says which system to ask for the log.
    """

    name: str
    bucket: str
    state: str
    workflow: str
    link: str
    build: str = ""  # Cloud Build id; empty for anything that came from GitHub

    @property
    def run_id(self) -> str | None:
        """The Actions run this check belongs to, or None if it did not come from Actions."""
        found = _RUN_ID_RE.search(self.link)
        return found.group(1) if found else None

    def line(self) -> str:
        """One display row: bucket, name (workflow), and the URL when there is a failure to open."""
        where = f" ({self.workflow})" if self.workflow and self.workflow != self.name else ""
        link = f"  {self.link}" if self.bucket == "fail" and self.link else ""
        return f"  {self.bucket:<8} {self.name}{where}{link}"

    def log(self, lines: int) -> str:
        """This run's failure text, tail-trimmed, from whichever system ran it."""
        if self.build:
            body = build_log(self.build, lines)
        elif self.run_id:
            result = run_gh("run", "view", self.run_id, "--log-failed", timeout=GH_TIMEOUT_S)
            body = result.stdout.strip() or result.stderr.strip() or "gh returned no log for this run"
        else:
            return f"{self.name} reported no run to fetch a log from — open its link above."
        tail = body.splitlines()[-lines:]
        return f"── {self.name}, last {len(tail)} log line(s) ──\n" + "\n".join(tail)


def run_gh(*args: str, timeout: float) -> subprocess.CompletedProcess[str]:
    """Run `gh` and capture it. A non-zero exit is data here (8 = pending, 1 = failing), not an error."""
    if GH is None:
        sys.exit("gh is not installed — a PR's CI state can only come from GitHub.")
    # S603: fixed argv from this module plus the branch/run id the caller passed; no shell.
    return subprocess.run((GH, *args), capture_output=True, text=True, check=False, timeout=timeout)  # noqa: S603


def run_gcloud(*args: str, timeout: float) -> subprocess.CompletedProcess[str]:
    """Run `gcloud` and capture it.

    Only reachable with gcloud present: `builds_region` and `fetch_builds` check `GCLOUD`, and
    `build_log` runs only against a build one of them already returned.
    """
    # S603: fixed argv from this module plus the region/commit/build id the caller passed; no shell.
    return subprocess.run((GCLOUD, *args), capture_output=True, text=True, check=False, timeout=timeout)  # noqa: S603


def builds_region(flag: str) -> str:
    """Which Cloud Build region to search, or "" when there is no gcloud to search with.

    Builds are regional and `gcloud builds list` defaults to `global`, so a repo that builds in
    `us-central1` answers an empty list to the default call — the one failure shape that reads as
    "nothing deployed". `gcloud config list` reports the region gcloud itself would use
    (`builds/region`, however it was set); `compute/region` is the fallback because a machine that
    deploys to one region has that one set. Both come from one call, tab-separated, and the tab is
    only stripped per field — stripping the whole line first would shift an unset `builds/region`
    and make the second leg unreachable. An empty result names the region it searched, so a wrong
    guess here is visible rather than silent.
    """
    if not GCLOUD:
        return ""
    if flag:
        return flag
    result = run_gcloud("config", "list", "--all", "--format=value(builds.region,compute.region)", timeout=GH_TIMEOUT_S)
    if result.returncode != 0:
        sys.stdout.write(f"gcloud could not resolve a Cloud Build region: {result.stderr.strip()[:200]}\n")
        return "global"
    configured, _, compute = result.stdout.partition("\t")
    return configured.strip() or compute.strip() or "global"


def build_log(build_id: str, lines: int) -> str:
    """The tail of a Cloud Build's log, read out of Cloud Logging.

    Not `gcloud builds log`: on the SDK this was written against (Google Cloud SDK 582.0.0) that
    command dies with `ERROR: gcloud crashed (KeyError): 'google/logging/type/log_severity.proto'`
    while `logging read` over the same build answers, so the log is read from the build's own log
    resource. `--order desc` asks for the newest entries — the tail the failure is in — so they
    come back newest-first and are flipped for reading.
    """
    query = f'resource.type="build" AND resource.labels.build_id="{build_id}"'
    result = run_gcloud(
        "logging",
        "read",
        query,
        "--order",
        "desc",
        "--limit",
        str(lines),
        "--format=value(textPayload)",
        timeout=GH_TIMEOUT_S,
    )
    body = result.stdout.strip() or result.stderr.strip() or "gcloud returned no log for this build"
    return "\n".join(reversed(body.splitlines()))


def fetch_builds(commit: str, region: str) -> list[Check]:
    """The Cloud Build builds triggered by `commit`, shaped like checks so the same code judges them.

    No builds is a real answer here — most repos deploy from Actions — so a machine without
    `gcloud`, a project without the API and a commit nothing built all return an empty list
    instead of a verdict. `deploy_targets` is the single place that decides what "nothing
    anywhere" means.
    """
    if not GCLOUD or not region:
        return []
    result = run_gcloud(
        "builds",
        "list",
        "--region",
        region,
        "--filter",
        f"substitutions.COMMIT_SHA={commit}",
        "--format=json",
        "--limit",
        "20",
        timeout=GH_TIMEOUT_S,
    )
    if result.returncode != 0:
        # Exits rather than returning [], for the reason `fetch_runs` exits on a broken `gh`: an
        # unauthenticated gcloud or a disabled API says nothing about whether the change is live,
        # and merging that silence with green Actions runs would report a deploy nobody read as
        # finished. Loud and exit 3; the fix is in the message.
        sys.stdout.write(
            f"gcloud could not list Cloud Build builds in {region}: {result.stderr.strip()[:200]}\n"
            "That is one of the two systems that could ship this commit left unread, so this is "
            "not a verdict. Fix the gcloud auth/project, or pass --region, and ask again.\n"
        )
        raise SystemExit(EXIT_UNKNOWN)
    items = json.loads(result.stdout) if result.stdout.strip() else []
    return [
        Check(
            name=item.get("substitutions", {}).get("TRIGGER_NAME") or f"build {item['id'][:8]}",
            bucket=_BUILD_STATUSES.get(item.get("status", ""), "pending"),
            state=item.get("status", ""),
            workflow=f"Cloud Build {region}",
            link=item.get("logUrl", ""),
            build=item["id"],
        )
        for item in items
    ]


def fetch_checks(branch: str) -> list[Check]:
    """Every check on `branch`'s PR (current branch when empty), worst bucket first.

    Exits 3 when the answer is an empty one — no PR, no checks configured, an unauthenticated `gh`.
    "I cannot tell you" is not "nothing failed", and a caller that treats zero checks as green
    would call an unbuilt branch verified.
    """
    target = (branch,) if branch else ()
    result = run_gh("pr", "checks", *target, "--json", "name,bucket,state,workflow,link", timeout=GH_TIMEOUT_S)
    items = json.loads(result.stdout) if result.stdout.strip() else []
    if not items:
        sys.stdout.write((result.stderr.strip() or "gh reported no checks for this branch") + "\n")
        raise SystemExit(EXIT_UNKNOWN)
    checks = [Check(**{field: item.get(field, "") for field in Check._fields}) for item in items]
    return sorted(checks, key=lambda check: (_BUCKET_ORDER.get(check.bucket, 9), check.name))


class Verdict(NamedTuple):
    """The exit code for a set of checks, and the one line that says why."""

    code: int
    summary: str


class Advice(NamedTuple):
    """What to do about each outcome, in the words of the stage being judged.

    The buckets are the same for a PR's checks and for a deploy's workflow runs; the next move is
    not. `{me}` interpolates this script's own invocation, so every sentence is a line the agent
    can paste.
    """

    red: str
    pending: str
    nothing_passed: str
    green: str


CHECK_ADVICE = Advice(
    red="Read the failure: `{me} logs`, fix it, push again.",
    pending="Block on it: `{me} wait`.",
    nothing_passed=(
        "Nothing actually ran. Find out why every check was skipped or cancelled before trusting this branch."
    ),
    # The whole point of the verb: green CI is the middle of the flow, and this is where an agent
    # otherwise reports the work as finished.
    green=(
        "Green CI is a reviewable branch, not a shipped change: `{me} merged` blocks until the PR "
        "leaves OPEN and `{me} deploy` blocks on what ships the merge. Launch them in the "
        "background (Bash `run_in_background: true`) — or launch `{me} ship`, which is all three "
        "stages in one backgroundable call."
    ),
)

DEPLOY_ADVICE = Advice(
    red="The deploy failed — the merge never reached production. Read the log below, fix it, ship again.",
    pending="Still rolling out: `{me} deploy` again to keep blocking on it, in the background.",
    nothing_passed=(
        "Every run or build was cancelled or skipped, so nothing was deployed — a superseded "
        "concurrency group or a path filter. The merge is in; the change is not live. Ship it."
    ),
    green=(
        "The deploy workflow finished. That is not proof the service is healthy — read its own logs "
        "and metrics for real traffic (error rate, latency, the code path you changed) before "
        "calling this done."
    ),
)


def verdict(checks: list[Check], advice: Advice = CHECK_ADVICE) -> Verdict:
    """Judge the checks: red beats pending beats green, because that is what has to be acted on."""
    counted = {bucket: sum(1 for check in checks if check.bucket == bucket) for bucket in _BUCKET_ORDER}
    tally = ", ".join(f"{count} {bucket}" for bucket, count in counted.items() if count)
    if counted["fail"]:
        return Verdict(EXIT_RED, f"RED — {tally}. " + advice.red.format(me=ME))
    if counted["pending"]:
        return Verdict(EXIT_PENDING, f"PENDING — {tally}. " + advice.pending.format(me=ME))
    # A run set that is entirely cancelled or skipped has no failure to report and nothing left to
    # wait for, and green is exactly the wrong word for it: for a deploy it means the change never
    # left the merge commit.
    if not counted["pass"]:
        return Verdict(EXIT_RED, f"NOT GREEN — {tally}. " + advice.nothing_passed.format(me=ME))
    return Verdict(EXIT_GREEN, f"GREEN — {tally}. " + advice.green.format(me=ME))


def report(checks: list[Check], advice: Advice = CHECK_ADVICE) -> int:
    """Print the checks and the verdict; return the exit code the verdict carries."""
    sys.stdout.write("\n".join(check.line() for check in checks) + "\n")
    judged = verdict(checks, advice)
    sys.stdout.write(judged.summary + "\n")
    return judged.code


def failing_logs(checks: list[Check], lines: int) -> str:
    """The failed steps of every red check, tail-trimmed, one block per run that failed.

    Keyed by the run, not the check: several checks share one Actions run, and printing its log
    once per check would fill the context with the same traceback.
    """
    failed: dict[str, Check] = {}
    for check in (check for check in checks if check.bucket == "fail"):
        failed.setdefault(check.build or check.run_id or check.name, check)
    return "\n\n".join(check.log(lines) for check in failed.values())


def watch(branch: str, seconds: float) -> bool:
    """Block until the checks conclude. True if they did, False if `seconds` ran out first.

    `gh`'s own `--watch` does the polling — one call that returns when the answer changes — so no
    caller ever has to write a sleep loop. Its output is a table redrawn on every refresh and is
    dropped on the floor; the state is read back afterwards from a plain `status` call.
    """
    target = (branch,) if branch else ()
    try:
        run_gh("pr", "checks", *target, "--watch", "--interval", str(WATCH_INTERVAL_S), timeout=seconds)
    except subprocess.TimeoutExpired:
        return False
    return True


class PullRequest(NamedTuple):
    """The PR of a branch, as far as the merge watch cares: is it still open, and what did it ship."""

    number: int
    state: str
    url: str
    merged_at: str
    merge_commit: str


def fetch_pr(branch: str) -> PullRequest:
    """The current state of `branch`'s PR (current branch when empty).

    Exits 3 when `gh` cannot answer — no PR, no auth, no network. "I could not look" must never
    read as "not merged yet", which would leave the agent watching a PR that shipped an hour ago.
    """
    target = (branch,) if branch else ()
    fields = "number,state,url,mergedAt,mergeCommit"
    result = run_gh("pr", "view", *target, "--json", fields, timeout=GH_TIMEOUT_S)
    if result.returncode != 0 or not result.stdout.strip():
        sys.stdout.write((result.stderr.strip() or "gh could not read a PR for this branch") + "\n")
        raise SystemExit(EXIT_UNKNOWN)
    data = json.loads(result.stdout)
    return PullRequest(
        number=data["number"],
        state=data["state"],
        url=data["url"],
        # Null on an open PR — the only two fields here that GitHub legitimately leaves empty.
        merged_at=data["mergedAt"] or "",
        merge_commit=(data["mergeCommit"] or {}).get("oid", ""),
    )


def report_merge(pr: PullRequest) -> int:
    """Say what happened to the PR, and what the merge has left to prove."""
    if pr.state != "MERGED":
        sys.stdout.write(f"CLOSED — PR #{pr.number} was closed without merging ({pr.url}). Nothing shipped.\n")
        return EXIT_RED
    sys.stdout.write(
        f"MERGED — PR #{pr.number} at {pr.merged_at}, merge commit {pr.merge_commit[:12]} ({pr.url}).\n"
        f"Merged is not deployed: `{ME} deploy` blocks on the workflows that run for that commit, "
        "and only after they are out do the service's own logs and metrics say whether the change "
        "works in production.\n"
    )
    return EXIT_GREEN


def fetch_runs(commit: str) -> list[Check]:
    """The Actions runs triggered by `commit`, shaped like checks so the same code can judge them.

    An empty list is a legitimate answer — plenty of repos deploy from outside Actions — but a
    `gh` that *failed* is not: it says nothing about how the repo ships, so it exits 3 rather than
    letting a broken lookup read as a repo with no deploy.
    """
    fields = "name,workflowName,status,conclusion,url"
    result = run_gh("run", "list", "--commit", commit, "--json", fields, "--limit", "20", timeout=GH_TIMEOUT_S)
    if result.returncode != 0:
        sys.stdout.write((result.stderr.strip() or "gh could not list the runs for this commit") + "\n")
        raise SystemExit(EXIT_UNKNOWN)
    items = json.loads(result.stdout) if result.stdout.strip() else []
    return [
        Check(
            name=item.get("name") or item.get("workflowName", ""),
            bucket=_RUN_CONCLUSIONS.get(item.get("conclusion") or "", "pending"),
            state=item.get("status", ""),
            workflow=item.get("workflowName", ""),
            link=item.get("url", ""),
        )
        for item in items
    ]


def deploy_targets(commit: str, region: str) -> list[Check]:
    """Everything that puts `commit` in production, from both systems this script can read.

    Both, not one or the other: a repo whose CI is Actions and whose deploy is Cloud Build has
    Actions runs for the merge commit that say nothing about whether the change is live, so
    judging only those would report a branch as shipped while the build that ships it is still
    queued. Worst bucket first, exactly like the PR's checks.
    """
    targets = fetch_runs(commit) + fetch_builds(commit, region)
    return sorted(targets, key=lambda target: (_BUCKET_ORDER.get(target.bucket, 9), target.name))


def awaited_builds(targets: list[Check], region: str) -> bool:
    """True while Cloud Build could still produce a build for this commit that is not listed yet.

    The whole false green this verb exists to stop lives here. A merge commit's Actions runs —
    the push-to-trunk CI — are green seconds after the merge, while the Cloud Build trigger takes
    seconds more to even create the build that ships the change. Judging the first listing would
    then report a deploy that does not exist yet as finished, with nothing pending to give it
    away. So while gcloud can be asked and has returned no build, the answer is "not yet".
    """
    return bool(GCLOUD) and bool(region) and not any(target.build for target in targets)


def await_deploy(commit: str, region: str, *, timeout: float, interval: float) -> list[Check]:
    """Block until nothing the merge commit set off is still running, then return the final state.

    A re-listing poll rather than a watch, deliberately: `gh run watch` only knows Actions, Cloud
    Build has no watch at all, and one loop over both is simpler than two waits spliced together.
    Re-listing is also the only thing that sees a build whose trigger had not fired yet when the
    merge landed — the normal case when this runs the moment `merged` returns, hence the grace
    window before any verdict is handed back.
    """
    deadline = time.monotonic() + timeout
    appear_by = time.monotonic() + BUILD_APPEAR_S
    targets: list[Check] = []
    while True:
        try:
            targets = deploy_targets(commit, region)
        except subprocess.TimeoutExpired as expired:
            # The one recoverable failure in this loop: the next listing answers, and the outer
            # deadline still bounds the wait. A 30-minute background wait must not die on a
            # single cold auth refresh — every other failure still raises.
            sys.stdout.write(f"{expired.cmd[0]} did not answer in {expired.timeout:.0f}s — re-asking.\n")
        else:
            settled = bool(targets) and not any(target.bucket == "pending" for target in targets)
            # A failure is final — no build appearing later turns a failed deploy green — so only
            # an otherwise-green verdict waits out the window.
            failed = any(target.bucket == "fail" for target in targets)
            if settled and (failed or not awaited_builds(targets, region)):
                return targets
            if time.monotonic() >= appear_by and (settled or not targets):
                return targets
        left = deadline - time.monotonic()
        if left <= 0:
            return targets
        time.sleep(min(interval, left))


def cmd_status(args: argparse.Namespace) -> int:
    """`status`: what CI says right now."""
    return report(fetch_checks(args.branch))


def report_with_logs(checks: list[Check], lines: int, advice: Advice = CHECK_ADVICE) -> int:
    """Report a run set, and print the failing steps' log when something in it failed."""
    code = report(checks, advice)
    if any(check.bucket == "fail" for check in checks):
        sys.stdout.write("\n" + failing_logs(checks, lines) + "\n")
    return code


def cmd_logs(args: argparse.Namespace) -> int:
    """`logs`: the failing steps, without the thousands of lines that passed."""
    return report_with_logs(fetch_checks(args.branch), args.lines)


def cmd_wait(args: argparse.Namespace) -> int:
    """`wait`: block until the run concludes, then report it — and its log when it went red."""
    if not watch(args.branch, args.timeout):
        sys.stdout.write(f"Still running after {args.timeout:.0f}s — reporting the state as it stands.\n")
    return cmd_logs(args)


def cmd_merged(args: argparse.Namespace) -> int:
    """`merged`: block until the PR leaves OPEN, re-asking on the poll interval."""
    deadline = time.monotonic() + args.timeout
    while True:
        pr = fetch_pr(args.branch)
        if pr.state != "OPEN":
            return report_merge(pr)
        left = deadline - time.monotonic()
        if left <= 0:
            sys.stdout.write(
                f"OPEN — PR #{pr.number} is still unmerged after {args.timeout:.0f}s ({pr.url}). Nothing "
                "shipped. If it is waiting on a reviewer, that is a person to ask, not a wait to extend.\n"
            )
            return EXIT_PENDING
        time.sleep(min(args.interval, left))


def cmd_deploy(args: argparse.Namespace) -> int:
    """`deploy`: block on everything the merge commit set off, then judge it like checks."""
    commit = fetch_pr(args.branch).merge_commit
    if not commit:
        sys.stdout.write("This PR has no merge commit — it is not merged yet. Run `merged` first.\n")
        return EXIT_PENDING
    region = builds_region(args.region)
    targets = await_deploy(commit, region, timeout=args.timeout, interval=args.interval)
    if not targets:
        sys.stdout.write(
            f"Nothing deploys {commit[:12]}: no Actions run, and no Cloud Build build in "
            f"{region or 'any region (no gcloud here)'}. Either this repo ships some other way — find "
            "out how, watch that — or the deploy is in a region this was not pointed at: `--region`.\n"
        )
        return EXIT_UNKNOWN
    return report_with_logs(targets, args.lines, DEPLOY_ADVICE)


def cmd_ship(args: argparse.Namespace) -> int:
    """`ship`: CI, then the merge, then the deploy — the whole arc in one process.

    The verb `run_in_background` was waiting for. An agent that has just pushed needs all three
    answers and needs none of them this second, so one launched process carries the branch to a
    live change and comes back once, instead of three foreground waits with the session frozen
    behind each. It stops at the first stage that is not green: there is nothing to watch
    downstream of a red CI or a PR nobody merged.
    """
    stages = (
        ("CI", cmd_wait, {"timeout": DEFAULT_WAIT_S}),
        ("MERGE", cmd_merged, {"timeout": DEFAULT_MERGE_WAIT_S, "interval": MERGE_POLL_S}),
        ("DEPLOY", cmd_deploy, {"timeout": DEFAULT_DEPLOY_WAIT_S, "interval": WATCH_INTERVAL_S, "region": args.region}),
    )
    for label, stage, flags in stages:
        sys.stdout.write(f"\n── {label} ──\n")
        code = stage(argparse.Namespace(branch=args.branch, lines=args.lines, **flags))
        if code != EXIT_GREEN:
            sys.stdout.write(f"\nChain stopped at {label} — nothing after it has happened yet.\n")
            return code
    return EXIT_GREEN


def parse_args(argv: list[str]) -> argparse.Namespace:
    """The six verbs and their flags.

    The shared flags hang off each verb rather than off the top level, so `ci.py logs --lines 5`
    — the order anyone actually types — parses. A bare `ci.py` means `status`.
    """
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--branch", default="", help="branch to inspect (default: the current one)")
    common.add_argument("--lines", type=int, default=DEFAULT_LOG_LINES, help="log lines kept per failing run")
    parser = argparse.ArgumentParser(
        description="CI for the current branch's PR: state, a bounded wait, and only the failing log.",
        allow_abbrev=False,
    )
    sub = parser.add_subparsers(dest="command")
    sub.add_parser("status", parents=[common], help="print every check and a verdict")
    sub.add_parser("logs", parents=[common], help="print the verdict plus the failing steps' log")
    waiting = sub.add_parser("wait", parents=[common], help="block until the checks conclude, then report")
    waiting.add_argument("--timeout", type=float, default=DEFAULT_WAIT_S, help="seconds to wait before reporting")
    merged = sub.add_parser("merged", parents=[common], help="block until the PR is merged or closed")
    merged.add_argument("--timeout", type=float, default=DEFAULT_MERGE_WAIT_S, help="seconds to wait before reporting")
    merged.add_argument("--interval", type=float, default=MERGE_POLL_S, help="seconds between merge checks")
    deploy = sub.add_parser("deploy", parents=[common], help="block on whatever ships the merge commit")
    deploy.add_argument("--timeout", type=float, default=DEFAULT_DEPLOY_WAIT_S, help="seconds to wait before reporting")
    deploy.add_argument("--interval", type=float, default=WATCH_INTERVAL_S, help="seconds between deploy re-checks")
    deploy.add_argument("--region", default="", help="Cloud Build region (default: gcloud's own)")
    ship = sub.add_parser("ship", parents=[common], help="CI, then the merge, then the deploy, in one call")
    ship.add_argument("--region", default="", help="Cloud Build region (default: gcloud's own)")
    return parser.parse_args(argv or ["status"])


def main(argv: list[str]) -> int:
    """Entry point: dispatch the verb, return its exit code."""
    args = parse_args(argv)
    verbs = {
        "status": cmd_status,
        "logs": cmd_logs,
        "wait": cmd_wait,
        "merged": cmd_merged,
        "deploy": cmd_deploy,
        "ship": cmd_ship,
    }
    return verbs[args.command](args)


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
