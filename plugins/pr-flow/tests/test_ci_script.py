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
GH_STUB = """#!/bin/sh
echo "$*" >> "$FAKE_GH_LOG"
case "$2" in
  view) printf '%s' "$FAKE_GH_RUN_LOG"; exit 0 ;;
esac
printf '%s' "$FAKE_GH_JSON"
exit "${FAKE_GH_EXIT:-0}"
"""


def check(name, bucket, link="https://github.test/o/r/actions/runs/77/job/9"):
    """One `gh pr checks --json` item."""
    return {"name": name, "bucket": bucket, "state": bucket, "workflow": "ci.yml", "link": link}


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

    def run(*args: str, checks=(), exit_code=0, run_log="") -> Ran:
        env = {
            **os.environ,
            "PATH": f"{bindir}{os.pathsep}{os.environ['PATH']}",
            "FAKE_GH_JSON": json.dumps(list(checks)),
            "FAKE_GH_EXIT": str(exit_code),
            "FAKE_GH_RUN_LOG": run_log,
            "FAKE_GH_LOG": str(log),
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
