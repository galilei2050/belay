"""What `scripts/ci.py` reports, and what it exits with, for each shape `gh` can answer in.

Run at the real boundary — the script as a subprocess, against a stub `gh` on PATH — because the
exit code and the printed verdict are the product here, and both come out of `gh`'s exit status
as much as its JSON.
"""

import json
import os
import subprocess
import sys
from pathlib import Path
from typing import NamedTuple

import pytest

SCRIPT = Path(__file__).parent.parent / "scripts" / "ci.py"

# `gh` answers from FAKE_GH_JSON / FAKE_GH_EXIT, and appends its argv to FAKE_GH_LOG so a test can
# assert which gh call the script actually made (a `wait` that never watches would pass otherwise).
# `pr view` answers FAKE_GH_PR_JSON, then FAKE_GH_PR_JSON2 on every later call, so a merge that
# lands mid-poll can be played back without a test that sleeps.
GH_STUB = """#!/bin/sh
echo "$*" >> "$FAKE_GH_LOG"
case "$1 $2" in
  "run view") printf '%s' "$FAKE_GH_RUN_LOG"; exit 0 ;;
  "run watch") exit 0 ;;
  "run list") printf '%s' "$FAKE_GH_RUNS_JSON"; exit 0 ;;
  "pr view")
    if [ "$(grep -c '^pr view' "$FAKE_GH_LOG")" -gt 1 ] && [ -n "$FAKE_GH_PR_JSON2" ]; then
      printf '%s' "$FAKE_GH_PR_JSON2"
    else
      printf '%s' "$FAKE_GH_PR_JSON"
    fi
    exit "${FAKE_GH_PR_EXIT:-0}" ;;
esac
printf '%s' "$FAKE_GH_JSON"
exit "${FAKE_GH_EXIT:-0}"
"""


def check(name, bucket, link="https://github.test/o/r/actions/runs/77/job/9"):
    """One `gh pr checks --json` item."""
    return {"name": name, "bucket": bucket, "state": bucket, "workflow": "ci.yml", "link": link}


def pr(state="OPEN", merge_commit="abcdef1234567890"):
    """One `gh pr view --json` answer."""
    return json.dumps(
        {
            "number": 370,
            "state": state,
            "url": "https://github.test/o/r/pull/370",
            "mergedAt": "2026-08-29T10:00:00Z" if state == "MERGED" else None,
            "mergeCommit": {"oid": merge_commit} if state == "MERGED" else None,
        }
    )


def deploy_run(name, conclusion, status="completed", run_id=88):
    """One `gh run list --json` item."""
    return {
        "name": name,
        "workflowName": name,
        "status": status,
        "conclusion": conclusion,
        "url": f"https://github.test/o/r/actions/runs/{run_id}",
    }


class Ran(NamedTuple):
    """One `ci.py` run: what it exited with, what it printed, and which gh calls it made."""

    code: int
    out: str
    calls: list[str]


@pytest.fixture
def ci(tmp_path):
    """Run `ci.py` against a stub `gh`."""
    bindir = tmp_path / "bin"
    bindir.mkdir()
    stub = bindir / "gh"
    stub.write_text(GH_STUB)
    stub.chmod(0o755)
    log = tmp_path / "gh.log"

    def run(*args: str, checks=(), exit_code=0, run_log="", pull="", pull_then="", runs=(), pr_exit=0) -> Ran:
        env = {
            **os.environ,
            "PATH": f"{bindir}{os.pathsep}{os.environ['PATH']}",
            "FAKE_GH_JSON": json.dumps(list(checks)),
            "FAKE_GH_EXIT": str(exit_code),
            "FAKE_GH_RUN_LOG": run_log,
            "FAKE_GH_LOG": str(log),
            "FAKE_GH_PR_JSON": pull,
            "FAKE_GH_PR_JSON2": pull_then,
            "FAKE_GH_PR_EXIT": str(pr_exit),
            "FAKE_GH_RUNS_JSON": json.dumps(list(runs)),
        }
        result = subprocess.run(  # noqa: S603
            (sys.executable, str(SCRIPT), *args), capture_output=True, text=True, env=env, check=False
        )
        calls = log.read_text().splitlines() if log.exists() else []
        return Ran(result.returncode, result.stdout, calls)

    return run


def test_all_green_exits_zero(ci):
    code, out, _ = ci("status", checks=[check("lint", "pass"), check("test", "pass")])
    assert code == 0
    assert "GREEN" in out


def test_a_failing_check_exits_red_and_points_at_the_log(ci):
    code, out, _ = ci("status", checks=[check("lint", "fail"), check("test", "pass")], exit_code=1)
    assert code == 1
    assert "RED" in out
    assert "logs" in out


def test_pending_is_its_own_exit_code(ci):
    """Pending is not green: a caller branching on the exit code must be able to tell them apart."""
    code, out, _ = ci("status", checks=[check("test", "pending")], exit_code=8)
    assert code == 2
    assert "PENDING" in out


def test_gh_saying_nothing_is_not_reported_as_green(ci):
    code, out, _ = ci("status", checks=(), exit_code=1)
    assert code == 3
    assert "GREEN" not in out


def test_failing_checks_are_listed_before_passing_ones(ci):
    _, out, _ = ci("status", checks=[check("a-pass", "pass"), check("z-fail", "fail")], exit_code=1)
    assert out.index("z-fail") < out.index("a-pass")


def test_logs_prints_only_the_tail_of_the_failing_run(ci):
    log = "\n".join(f"line {n}" for n in range(200))
    _, out, calls = ci("logs", "--lines", "5", checks=[check("test", "fail")], exit_code=1, run_log=log)
    assert "line 199" in out
    assert "line 100" not in out
    assert any("run view 77 --log-failed" in call for call in calls)


def test_logs_stays_quiet_about_logs_when_nothing_failed(ci):
    _, out, calls = ci("logs", checks=[check("test", "pass")])
    assert "log line" not in out
    assert not any("run view" in call for call in calls)


def test_wait_blocks_in_gh_rather_than_polling(ci):
    """The watch is one `gh` call; the state is read back once afterwards — never a re-poll loop."""
    _, _, calls = ci("wait", checks=[check("test", "pass")])
    assert any("--watch" in call for call in calls)
    assert sum(1 for call in calls if call.startswith("pr checks") and "--watch" not in call) == 1


def test_a_named_branch_is_passed_through_to_gh(ci):
    _, _, calls = ci("status", "--branch", "feature-x", checks=[check("test", "pass")])
    assert any("feature-x" in call for call in calls)


def test_green_checks_point_at_the_merge_watch(ci):
    """Green CI is where an agent stops, so the green verdict itself has to name the next link."""
    _, out, _ = ci("status", checks=[check("test", "pass")])
    assert "merged" in out


def test_merged_reports_the_merge_commit_and_the_deploy_as_the_next_step(ci):
    code, out, _ = ci("merged", pull=pr("MERGED"))
    assert code == 0
    assert "MERGED" in out
    assert "abcdef123456" in out
    assert "deploy" in out


def test_merged_keeps_asking_until_the_pr_leaves_open(ci):
    """The one verb that really polls: an open PR is re-read, not reported as a verdict."""
    code, out, calls = ci("merged", "--interval", "0", pull=pr("OPEN"), pull_then=pr("MERGED"))
    assert code == 0
    assert "MERGED" in out
    assert sum(1 for call in calls if call.startswith("pr view")) == 2


def test_a_pr_still_open_at_the_timeout_is_pending_not_shipped(ci):
    code, out, _ = ci("merged", "--timeout", "0", pull=pr("OPEN"))
    assert code == 2
    assert "OPEN" in out
    assert "MERGED" not in out


def test_a_closed_pr_is_red(ci):
    code, out, _ = ci("merged", "--timeout", "0", pull=pr("CLOSED"))
    assert code == 1
    assert "CLOSED" in out


def test_gh_unable_to_read_the_pr_is_not_reported_as_unmerged(ci):
    code, out, _ = ci("merged", "--timeout", "0", pull="", pr_exit=1)
    assert code == 3
    assert "MERGED" not in out
    assert "OPEN" not in out


def test_deploy_watches_the_merge_commits_runs_and_judges_them(ci):
    code, out, calls = ci("deploy", pull=pr("MERGED"), runs=[deploy_run("deploy", "success")])
    assert code == 0
    assert "GREEN" in out
    assert "metrics" in out
    assert any("run list --commit abcdef1234567890" in call for call in calls)


def test_deploy_blocks_on_a_run_that_has_not_concluded(ci):
    _, _, calls = ci("deploy", pull=pr("MERGED"), runs=[deploy_run("deploy", None, status="in_progress")])
    assert any("run watch 88" in call for call in calls)


def test_a_failed_deploy_is_red_and_carries_its_log(ci):
    log = "\n".join(f"line {n}" for n in range(80))
    code, out, _ = ci("deploy", "--lines", "3", pull=pr("MERGED"), runs=[deploy_run("deploy", "failure")], run_log=log)
    assert code == 1
    assert "RED" in out
    assert "line 79" in out


def test_a_repo_that_deploys_outside_actions_is_not_reported_as_shipped(ci):
    code, out, _ = ci("deploy", pull=pr("MERGED"), runs=())
    assert code == 3
    assert "GREEN" not in out


def test_deploy_refuses_to_guess_a_commit_for_an_unmerged_pr(ci):
    code, out, _ = ci("deploy", pull=pr("OPEN"))
    assert code == 3
    assert "not merged" in out
