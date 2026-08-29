#!/usr/bin/env python3
"""CI for the current branch's PR: one line of state, a bounded wait, and only the failing log.

The agent already has `gh`. What it does not have is a shape of `gh` that fits a context window:
`gh pr checks --watch` redraws a table on every refresh, `--log-failed` needs a run id that only
exists inside a check's URL, and a raw job log is thousands of lines whose last forty are the
failure. Left to improvise, an agent either polls `gh pr checks` in a wait loop or pastes a whole
log into its context — and usually skips the step entirely and calls a branch done on a green
push.

So: five verbs, each ending in a statement the agent can act on.

* `status` — every check, one line each, plus a verdict. One `gh` call.
* `wait`   — blocks in `gh`'s own watch until the run concludes (hard cap: `--timeout`), then
             prints the verdict and, when red, the failing log. Not a poll loop: one call that
             returns when there is something to say.
* `logs`   — the failing steps only, tail-trimmed, per run.
* `merged` — blocks until the PR leaves OPEN, then says what shipped and what to watch next.
* `deploy` — blocks on the Actions runs of the merge commit — the workflows that put the change
             in production — judged and log-trimmed exactly like the PR's checks.

The last two exist because green CI is where an agent stops, and a green branch is not a shipped
change. The chain is CI → merge → deploy → the service's own metrics, and each verb ends by
naming the next link so the agent does not mistake a passing signal for a working system.

Exit codes are the summary, so a caller can branch without parsing prose: 0 green, 1 red,
2 still pending, 3 nothing to report on (no PR, no checks, no `gh`).
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
import sys
import time
from typing import NamedTuple

GH = shutil.which("gh")

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

EXIT_GREEN, EXIT_RED, EXIT_PENDING, EXIT_UNKNOWN = 0, 1, 2, 3


class Check(NamedTuple):
    """One check run on the PR, as `gh pr checks --json` reports it."""

    name: str
    bucket: str
    state: str
    workflow: str
    link: str

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


def run_gh(*args: str, timeout: float) -> subprocess.CompletedProcess[str]:
    """Run `gh` and capture it. A non-zero exit is data here (8 = pending, 1 = failing), not an error."""
    if GH is None:
        sys.exit("gh is not installed — a PR's CI state can only come from GitHub.")
    # S603: fixed argv from this module plus the branch/run id the caller passed; no shell.
    return subprocess.run((GH, *args), capture_output=True, text=True, check=False, timeout=timeout)  # noqa: S603


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
    green: str


CHECK_ADVICE = Advice(
    red="Read the failure: `{me} logs`, fix it, push again.",
    pending="Block on it: `{me} wait`.",
    # The whole point of the verb: green CI is the middle of the flow, and this is where an agent
    # otherwise reports the work as finished.
    green=(
        "Green CI is a reviewable branch, not a shipped change: `{me} merged` blocks until the PR "
        "leaves OPEN, re-checking every 5 minutes, and says what to watch next."
    ),
)

DEPLOY_ADVICE = Advice(
    red="The deploy failed — the merge never reached production. Read the log below, fix it, ship again.",
    pending="Still rolling out: `{me} deploy` again to keep blocking on it.",
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
    me = f"python3 {sys.argv[0]}"
    if counted["fail"]:
        return Verdict(EXIT_RED, f"RED — {tally}. " + advice.red.format(me=me))
    if counted["pending"]:
        return Verdict(EXIT_PENDING, f"PENDING — {tally}. " + advice.pending.format(me=me))
    return Verdict(EXIT_GREEN, f"GREEN — {tally}. " + advice.green.format(me=me))


def report(checks: list[Check], advice: Advice = CHECK_ADVICE) -> int:
    """Print the checks and the verdict; return the exit code the verdict carries."""
    sys.stdout.write("\n".join(check.line() for check in checks) + "\n")
    judged = verdict(checks, advice)
    sys.stdout.write(judged.summary + "\n")
    return judged.code


def failing_logs(checks: list[Check], lines: int) -> str:
    """The failed steps of every red check, tail-trimmed, one block per Actions run."""
    blocks: list[str] = []
    for run_id in dict.fromkeys(check.run_id for check in checks if check.bucket == "fail"):
        if run_id is None:
            blocks.append("A failing check reported no Actions run — open its link above for the log.")
            continue
        result = run_gh("run", "view", run_id, "--log-failed", timeout=GH_TIMEOUT_S)
        body = result.stdout.strip() or result.stderr.strip() or "gh returned no log for this run"
        tail = body.splitlines()[-lines:]
        blocks.append(f"── run {run_id}, last {len(tail)} log line(s) ──\n" + "\n".join(tail))
    return "\n\n".join(blocks)


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
        number=data.get("number", 0),
        state=data.get("state", ""),
        url=data.get("url", ""),
        merged_at=data.get("mergedAt") or "",
        merge_commit=(data.get("mergeCommit") or {}).get("oid", ""),
    )


def report_merge(pr: PullRequest) -> int:
    """Say what happened to the PR, and what the merge has left to prove."""
    me = f"python3 {sys.argv[0]}"
    if pr.state != "MERGED":
        sys.stdout.write(f"CLOSED — PR #{pr.number} was closed without merging ({pr.url}). Nothing shipped.\n")
        return EXIT_RED
    sys.stdout.write(
        f"MERGED — PR #{pr.number} at {pr.merged_at}, merge commit {pr.merge_commit[:12]} ({pr.url}).\n"
        f"Merged is not deployed: `{me} deploy` blocks on the workflows that run for that commit, "
        "and only after they are out do the service's own logs and metrics say whether the change "
        "works in production.\n"
    )
    return EXIT_GREEN


def fetch_runs(commit: str) -> list[Check]:
    """The Actions runs triggered by `commit`, shaped like checks so the same code can judge them.

    Exits 3 when there are none: a repo that deploys from outside Actions is a repo this script
    cannot watch, and saying so is the only honest answer — reporting zero runs as green would
    call an undeployed change live.
    """
    fields = "name,workflowName,status,conclusion,url"
    result = run_gh("run", "list", "--commit", commit, "--json", fields, "--limit", "20", timeout=GH_TIMEOUT_S)
    items = json.loads(result.stdout) if result.stdout.strip() else []
    if not items:
        sys.stdout.write(
            f"No Actions run for {commit[:12]} — this repo ships some other way. Find how the merge "
            "reaches production, watch that, then read the service's logs and metrics.\n"
        )
        raise SystemExit(EXIT_UNKNOWN)
    runs = [
        Check(
            name=item.get("name") or item.get("workflowName", ""),
            bucket=_RUN_CONCLUSIONS.get(item.get("conclusion") or "", "pending"),
            state=item.get("status", ""),
            workflow=item.get("workflowName", ""),
            link=item.get("url", ""),
        )
        for item in items
    ]
    return sorted(runs, key=lambda run: (_BUCKET_ORDER.get(run.bucket, 9), run.name))


def cmd_status(args: argparse.Namespace) -> int:
    """`status`: what CI says right now."""
    return report(fetch_checks(args.branch))


def cmd_logs(args: argparse.Namespace) -> int:
    """`logs`: the failing steps, without the thousands of lines that passed."""
    checks = fetch_checks(args.branch)
    code = report(checks)
    if code == EXIT_RED:
        sys.stdout.write("\n" + failing_logs(checks, args.lines) + "\n")
    return code


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
    """`deploy`: block on the Actions runs of the merge commit, then judge them like checks."""
    commit = args.commit or fetch_pr(args.branch).merge_commit
    if not commit:
        sys.stdout.write("This PR has no merge commit — it is not merged yet. Run `merged` first.\n")
        return EXIT_UNKNOWN
    deadline = time.monotonic() + args.timeout
    for run_id in dict.fromkeys(run.run_id for run in fetch_runs(commit) if run.bucket == "pending"):
        left = deadline - time.monotonic()
        if run_id is None or left <= 0:
            break
        try:
            run_gh("run", "watch", run_id, "--interval", str(WATCH_INTERVAL_S), timeout=left)
        except subprocess.TimeoutExpired:
            sys.stdout.write(f"Deploy run {run_id} still going after {args.timeout:.0f}s — reporting as it stands.\n")
            break
    runs = fetch_runs(commit)
    code = report(runs, DEPLOY_ADVICE)
    if code == EXIT_RED:
        sys.stdout.write("\n" + failing_logs(runs, args.lines) + "\n")
    return code


def parse_args(argv: list[str]) -> argparse.Namespace:
    """The five verbs and their flags.

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
    deploy = sub.add_parser("deploy", parents=[common], help="block on the merge commit's workflow runs")
    deploy.add_argument("--timeout", type=float, default=DEFAULT_DEPLOY_WAIT_S, help="seconds to wait before reporting")
    deploy.add_argument("--commit", default="", help="commit to watch (default: the PR's merge commit)")
    return parser.parse_args(argv or ["status"])


def main(argv: list[str]) -> int:
    """Entry point: dispatch the verb, return its exit code."""
    args = parse_args(argv)
    verbs = {"status": cmd_status, "logs": cmd_logs, "wait": cmd_wait, "merged": cmd_merged, "deploy": cmd_deploy}
    return verbs[args.command](args)


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
