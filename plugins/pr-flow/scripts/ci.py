#!/usr/bin/env python3
"""CI for the current branch's PR: one line of state, a bounded wait, and only the failing log.

The agent already has `gh`. What it does not have is a shape of `gh` that fits a context window:
`gh pr checks --watch` redraws a table on every refresh, `--log-failed` needs a run id that only
exists inside a check's URL, and a raw job log is thousands of lines whose last forty are the
failure. Left to improvise, an agent either polls `gh pr checks` in a wait loop or pastes a whole
log into its context — and usually skips the step entirely and calls a branch done on a green
push.

So: three verbs, each ending in a statement the agent can act on.

* `status` — every check, one line each, plus a verdict. One `gh` call.
* `wait`   — blocks in `gh`'s own watch until the run concludes (hard cap: `--timeout`), then
             prints the verdict and, when red, the failing log. Not a poll loop: one call that
             returns when there is something to say.
* `logs`   — the failing steps only, tail-trimmed, per run.

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

# The run id lives only in the check's URL: .../actions/runs/<run>/job/<job>. A check from outside
# Actions (a status posted by an external service) has no run and no log to fetch.
_RUN_ID_RE = re.compile(r"/actions/runs/(\d+)")

# Worst first — the failing check is what the reader came for, and a long list scrolls the top away.
_BUCKET_ORDER = {"fail": 0, "pending": 1, "cancel": 2, "skipping": 3, "pass": 4}

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


def verdict(checks: list[Check]) -> Verdict:
    """Judge the checks: red beats pending beats green, because that is what has to be acted on."""
    counted = {bucket: sum(1 for check in checks if check.bucket == bucket) for bucket in _BUCKET_ORDER}
    tally = ", ".join(f"{count} {bucket}" for bucket, count in counted.items() if count)
    me = f"python3 {sys.argv[0]}"
    if counted["fail"]:
        return Verdict(EXIT_RED, f"RED — {tally}. Read the failure: `{me} logs`, fix it, push again.")
    if counted["pending"]:
        return Verdict(EXIT_PENDING, f"PENDING — {tally}. Block on it: `{me} wait`.")
    return Verdict(EXIT_GREEN, f"GREEN — {tally}.")


def report(checks: list[Check]) -> int:
    """Print the checks and the verdict; return the exit code the verdict carries."""
    sys.stdout.write("\n".join(check.line() for check in checks) + "\n")
    judged = verdict(checks)
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


def parse_args(argv: list[str]) -> argparse.Namespace:
    """The three verbs and their flags.

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
    return parser.parse_args(argv or ["status"])


def main(argv: list[str]) -> int:
    """Entry point: dispatch the verb, return its exit code."""
    args = parse_args(argv)
    return {"status": cmd_status, "logs": cmd_logs, "wait": cmd_wait}[args.command](args)


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
