"""What `scripts/ci.py` reports, and what it exits with, for each shape `gh` can answer in.

Run at the real boundary — the script as a subprocess, against a stub `gh` on PATH — because the
exit code and the printed verdict are the product here, and both come out of `gh`'s exit status
as much as its JSON.
"""

import importlib.util
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
  "run list")
    if [ "$(grep -c '^run list' "$FAKE_GH_LOG")" -gt 1 ] && [ -n "$FAKE_GH_RUNS_JSON2" ]; then
      printf '%s' "$FAKE_GH_RUNS_JSON2"
    else
      printf '%s' "$FAKE_GH_RUNS_JSON"
    fi
    exit "${FAKE_GH_RUNS_EXIT:-0}" ;;
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

# The Cloud Build half. It logs into the same file as the `gh` stub, prefixed with its own name,
# so one `calls` list shows the order the script asked its two CLIs in — and an assertion about a
# `gcloud` call cannot be satisfied by a `gh` one.
GCLOUD_STUB = """#!/bin/sh
echo "gcloud $*" >> "$FAKE_GH_LOG"
case "$1 $2" in
  "config list") printf '%s\\t%s' "$FAKE_GCLOUD_BUILDS_REGION" "$FAKE_GCLOUD_COMPUTE_REGION"; exit 0 ;;
  "builds list")
    if [ "$(grep -c '^gcloud builds list' "$FAKE_GH_LOG")" -gt 1 ] && [ -n "$FAKE_GCLOUD_BUILDS_JSON2" ]; then
      printf '%s' "$FAKE_GCLOUD_BUILDS_JSON2"
    else
      printf '%s' "$FAKE_GCLOUD_BUILDS_JSON"
    fi
    exit "${FAKE_GCLOUD_BUILDS_EXIT:-0}" ;;
  "logging read") printf '%s' "$FAKE_GCLOUD_BUILD_LOG"; exit 0 ;;
esac
echo "unexpected gcloud call: $*" >&2
exit 99
"""


def check(name, bucket, link="https://github.test/o/r/actions/runs/77/job/9"):
    """One `gh pr checks --json` item."""
    return {"name": name, "bucket": bucket, "state": bucket, "workflow": "ci.yml", "link": link}


def pr(state="OPEN", merge_commit="abcdef123456ffffffffffffffffffffffffffff"):
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


def cloud_build(status, trigger="backend-deployment", build_id="c1f00931-e877-4a33-9333-f39e0da7edb9"):
    """One `gcloud builds list --format=json` item."""
    return {
        "id": build_id,
        "status": status,
        "logUrl": f"https://console.cloud.test/cloud-build/builds/{build_id}",
        "substitutions": {"TRIGGER_NAME": trigger, "COMMIT_SHA": "abcdef123456ffffffffffffffffffffffffffff"},
    }


class Ran(NamedTuple):
    """One `ci.py` run: what it exited with, what it printed, and which CLI calls it made."""

    code: int
    out: str
    calls: list[str]


@pytest.fixture
def ci(tmp_path):
    """Run `ci.py` against a stub `gh` and — unless a test passes `gcloud=False` — a stub `gcloud`.

    Dropping the stub is not enough to simulate a machine without `gcloud`: `shutil.which` would
    walk on and find the installed one. So that case also takes every directory holding a real
    `gcloud` out of `PATH`, while keeping the rest — the stubs themselves need `grep` and `printf`.
    """
    bindir = tmp_path / "bin"
    bindir.mkdir()
    log = tmp_path / "cli.log"
    for name, body in (("gh", GH_STUB), ("gcloud", GCLOUD_STUB)):
        stub = bindir / name
        stub.write_text(body)
        stub.chmod(0o755)

    def path(*, with_gcloud: bool) -> str:
        """`PATH` with the stub dir in front, and without the real gcloud when a test asks."""
        inherited = os.environ["PATH"].split(os.pathsep)
        if not with_gcloud:
            inherited = [entry for entry in inherited if not (Path(entry) / "gcloud").exists()]
        return os.pathsep.join([str(bindir), *inherited])

    # One keyword per shape the CLIs can answer in; the stubs read them out of the environment.
    def run(
        *args: str,
        gcloud=True,
        checks=(),
        exit_code=0,
        run_log="",
        pull="",
        pull_then="",
        runs=(),
        runs_then=(),
        pr_exit=0,
        runs_exit=0,
        builds=(),
        builds_then=(),
        builds_exit=0,
        build_log="",
        builds_region="",
        compute_region="us-central1",
        appear=0,
    ) -> Ran:
        if not gcloud:
            (bindir / "gcloud").unlink()
        env = {
            **os.environ,
            "PATH": path(with_gcloud=gcloud),
            "FAKE_GH_JSON": json.dumps(list(checks)),
            "FAKE_GH_EXIT": str(exit_code),
            "FAKE_GH_RUN_LOG": run_log,
            "FAKE_GH_LOG": str(log),
            "FAKE_GH_PR_JSON": pull,
            "FAKE_GH_PR_JSON2": pull_then,
            "FAKE_GH_PR_EXIT": str(pr_exit),
            "FAKE_GH_RUNS_JSON": json.dumps(list(runs)),
            "FAKE_GH_RUNS_JSON2": json.dumps(list(runs_then)) if runs_then else "",
            "FAKE_GH_RUNS_EXIT": str(runs_exit),
            "FAKE_GCLOUD_BUILDS_JSON": json.dumps(list(builds)),
            "FAKE_GCLOUD_BUILDS_JSON2": json.dumps(list(builds_then)) if builds_then else "",
            "FAKE_GCLOUD_BUILDS_EXIT": str(builds_exit),
            "FAKE_GCLOUD_BUILD_LOG": build_log,
            "FAKE_GCLOUD_BUILDS_REGION": builds_region,
            "FAKE_GCLOUD_COMPUTE_REGION": compute_region,
            # Off unless a test is about the window itself: every other deploy test would
            # otherwise spend two minutes waiting for a Cloud Build build it never stubbed.
            "PR_FLOW_BUILD_APPEAR_S": str(appear),
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
    assert "abcdef123456 " in out  # trimmed to 12 chars, not the whole 40-char oid
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
    assert any("run list --commit abcdef123456ffffffffffffffffffffffffffff" in call for call in calls)


def test_a_run_that_has_not_concluded_is_pending_not_shipped(ci):
    """A run with no conclusion is pending — the one input a bucket tally would read as green."""
    code, out, _ = ci("deploy", "--timeout", "0", pull=pr("MERGED"), runs=[deploy_run("d", None, status="queued")])
    assert code == 2
    assert "PENDING" in out


def test_deploy_keeps_re_reading_until_the_run_concludes(ci):
    """The wait is a re-listing poll, so the verdict has to come from the later answer, not the first."""
    code, out, calls = ci(
        "deploy",
        "--interval",
        "0",
        pull=pr("MERGED"),
        runs=[deploy_run("deploy", None, status="in_progress")],
        runs_then=[deploy_run("deploy", "success")],
    )
    assert code == 0
    assert "GREEN" in out
    assert sum(1 for call in calls if call.startswith("run list")) == 2


def test_a_deploy_that_was_only_cancelled_or_skipped_is_not_reported_as_shipped(ci):
    """Nothing succeeded, so nothing is live — the one input where a bucket tally would read green."""
    code, out, _ = ci("deploy", pull=pr("MERGED"), runs=[deploy_run("deploy", "cancelled")])
    assert code == 1
    assert "NOT GREEN" in out
    assert "the change is not live" in out


def test_a_failed_deploy_is_red_and_carries_its_log(ci):
    log = "\n".join(f"line {n}" for n in range(80))
    code, out, _ = ci("deploy", "--lines", "3", pull=pr("MERGED"), runs=[deploy_run("deploy", "failure")], run_log=log)
    assert code == 1
    assert "RED" in out
    assert "line 79" in out
    assert "line 76" not in out


def test_gh_unable_to_list_the_runs_is_not_reported_as_a_repo_without_actions(ci):
    """An `gh` that failed says nothing about how the repo deploys, and its own error must survive."""
    code, out, _ = ci("deploy", pull=pr("MERGED"), runs=(), runs_exit=1)
    assert code == 3
    assert "ships some other way" not in out


def test_a_repo_that_deploys_outside_both_systems_is_not_reported_as_shipped(ci):
    """And it says where it looked: an empty answer the reader cannot place is one they mis-trust."""
    code, out, _ = ci("deploy", "--timeout", "0", pull=pr("MERGED"), runs=(), builds=())
    assert code == 3
    assert "GREEN" not in out
    assert "us-central1" in out


def test_a_cloud_build_deploy_is_judged_like_a_workflow_run(ci):
    """A repo that ships from Cloud Build and nothing else still gets a verdict, not a shrug."""
    code, out, calls = ci("deploy", pull=pr("MERGED"), runs=(), builds=[cloud_build("SUCCESS")])
    assert code == 0
    assert "GREEN" in out
    assert "backend-deployment" in out
    assert any("substitutions.COMMIT_SHA=abcdef123456" in call for call in calls if call.startswith("gcloud builds"))


def test_a_running_cloud_build_keeps_the_deploy_pending_while_actions_is_already_green(ci):
    """The false green this exists to stop: Actions ran the tests, Cloud Build is still shipping."""
    code, out, _ = ci(
        "deploy",
        "--timeout",
        "0",
        pull=pr("MERGED"),
        runs=[deploy_run("ci", "success")],
        builds=[cloud_build("WORKING")],
    )
    assert code == 2
    assert "PENDING" in out


def test_a_failed_cloud_build_carries_its_log_from_cloud_logging(ci):
    """`gcloud builds log` crashes on some SDK installs, so the log comes from the build's log resource."""
    newest_first = "\n".join(f"line {n}" for n in reversed(range(80)))
    code, out, calls = ci(
        "deploy", "--lines", "3", pull=pr("MERGED"), runs=(), builds=[cloud_build("FAILURE")], build_log=newest_first
    )
    assert code == 1
    assert "RED" in out
    assert "line 79" in out
    assert "line 76" not in out
    assert any(call.startswith("gcloud logging read") and "--order desc" in call for call in calls)
    assert not any(call.startswith("gcloud builds log") for call in calls)


def test_a_named_region_is_used_instead_of_asking_gcloud_for_one(ci):
    _, _, calls = ci("deploy", "--region", "europe-west1", pull=pr("MERGED"), runs=(), builds=[cloud_build("SUCCESS")])
    assert any("builds list --region europe-west1" in call for call in calls)
    assert not any(call.startswith("gcloud config list") for call in calls)


def test_a_gcloud_that_cannot_list_builds_is_not_a_green_verdict(ci):
    """One of the two systems that could ship the commit went unread, so GREEN is not available."""
    code, out, _ = ci("deploy", pull=pr("MERGED"), runs=[deploy_run("ci", "success")], builds=(), builds_exit=1)
    assert code == 3
    assert "could not list Cloud Build builds in us-central1" in out
    assert "GREEN" not in out


def test_a_deploy_is_not_green_until_cloud_build_has_had_time_to_create_its_build(ci):
    """The false green this whole half exists to stop.

    A merge commit's Actions runs — the push-to-trunk CI — go green seconds after the merge, while
    the trigger has not yet created the build that ships the change. Judging the first listing
    would report a deploy that does not exist as finished.
    """
    code, out, calls = ci(
        "deploy",
        "--interval",
        "0",
        pull=pr("MERGED"),
        runs=[deploy_run("ci", "success")],
        builds=(),
        builds_then=[cloud_build("SUCCESS")],
        appear=30,
    )
    assert code == 0
    assert "backend-deployment" in out
    assert sum(1 for call in calls if call.startswith("gcloud builds list")) == 2


@pytest.mark.parametrize(
    ("status", "expected", "verdict_word"),
    [("CANCELLED", 1, "NOT GREEN"), ("TIMEOUT", 1, "RED"), ("EXPIRED", 1, "RED"), ("QUEUED", 2, "PENDING")],
)
def test_no_build_outcome_short_of_success_is_reported_as_shipped(ci, status, expected, verdict_word):
    """Each of these reads as shipped under a bucket table that maps it wrong, and none of them is."""
    code, out, _ = ci("deploy", "--timeout", "0", pull=pr("MERGED"), runs=(), builds=[cloud_build(status)])
    assert code == expected
    assert verdict_word in out


@pytest.mark.parametrize(
    ("configured", "compute", "expected"),
    [("europe-west1", "us-central1", "europe-west1"), ("", "us-central1", "us-central1"), ("", "", "global")],
)
def test_the_region_falls_back_from_builds_to_compute_to_global(ci, configured, compute, expected):
    """gcloud's own build region first, the machine's compute region next, gcloud's default last."""
    _, _, calls = ci(
        "deploy",
        "--timeout",
        "0",
        pull=pr("MERGED"),
        runs=(),
        builds=(),
        builds_region=configured,
        compute_region=compute,
    )
    assert any(f"builds list --region {expected} " in call for call in calls)


def test_without_gcloud_the_report_does_not_name_a_region_nobody_queried(ci):
    """`--region` is not a search: with no gcloud on PATH nothing was asked, and saying so is the answer."""
    code, out, calls = ci(
        "deploy", "--region", "us-central1", "--timeout", "0", pull=pr("MERGED"), runs=(), gcloud=False
    )
    assert code == 3
    assert "us-central1" not in out
    assert not any(call.startswith("gcloud") for call in calls)


def test_ship_runs_the_whole_arc_in_one_call(ci):
    code, out, _ = ci("ship", checks=[check("test", "pass")], pull=pr("MERGED"), runs=[deploy_run("deploy", "success")])
    assert code == 0
    assert [line.strip(" ─") for line in out.splitlines() if line.startswith("── ")] == ["CI", "MERGE", "DEPLOY"]


def test_ship_stops_at_the_first_stage_that_is_not_green(ci):
    """Nothing downstream of a red CI has happened, so nothing downstream of it may be reported."""
    code, out, _ = ci("ship", checks=[check("test", "fail")], exit_code=1, pull=pr("MERGED"))
    assert code == 1
    assert "Chain stopped at CI" in out
    assert "MERGE" not in out


def test_deploy_refuses_to_guess_a_commit_for_an_unmerged_pr(ci):
    """Not merged yet is "not ready", not "I could not look" — the codes have to tell them apart."""
    code, out, _ = ci("deploy", pull=pr("OPEN"))
    assert code == 2
    assert "not merged" in out


def test_every_outcome_maps_onto_a_bucket_the_verdict_knows():
    """An outcome mapped to a bucket nobody counts would report a broken deploy as green."""
    spec = importlib.util.spec_from_file_location("ci_module", SCRIPT)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    assert set(module._RUN_CONCLUSIONS.values()) <= set(module._BUCKET_ORDER)
    assert set(module._BUILD_STATUSES.values()) <= set(module._BUCKET_ORDER)
