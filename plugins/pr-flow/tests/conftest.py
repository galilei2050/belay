"""Test fixtures for pr-flow.

The hook is exercised at its real boundary: the script Claude Code runs, fed a JSON payload on
stdin, against a real git repo with a real remote. `gh` is the one thing faked — a stub on PATH
— because the whole decision hinges on telling "no PR here" apart from "gh cannot answer", and
only a stub produces both on demand.
"""

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import TypedDict

import pytest

HOOKS_DIR = Path(__file__).parent.parent / "hooks"


class NudgePayload(TypedDict):
    """The `hookSpecificOutput` half of a PostToolUse nudge."""

    hookEventName: str
    additionalContext: str


class HookOutput(TypedDict, total=False):
    """Everything either hook can write: a PostToolUse nudge, or a blocked Stop."""

    hookSpecificOutput: NudgePayload
    decision: str
    reason: str


# The `gh` stub. It asserts its own argv first, so a hook that changes the subcommand or drops a
# `--json` field turns the suite red instead of silently passing against a stub that answers
# anything. Behaviour after that is chosen by FAKE_GH_MODE in the hook's environment.
GH_STUB = """#!/bin/sh
case "$1 $2 $5 $6 $7" in
  "pr list --state open --json") ;;
  *) echo "unexpected gh call: $*" >&2; exit 99 ;;
esac
case "$FAKE_GH_MODE" in
  open)   printf '[{"number":12,"url":"https://example.test/pr/12"}]'; exit 0 ;;
  unauth) echo "gh: To get started with GitHub CLI, please run: gh auth login" >&2; exit 4 ;;
  hang)   sleep 30 ;;
  *)      printf '[]'; exit 0 ;;
esac
"""


@pytest.fixture(autouse=True)
def clean_git_env(monkeypatch):
    """Drop inherited `GIT_*` vars so the fixture repo is not hijacked by an outer git process.

    The pre-push hook runs these tests *inside* git, which exports GIT_DIR and GIT_INDEX_FILE;
    without this the fixture's commits would land in belay itself.
    """
    for name in [key for key in os.environ if key.startswith("GIT_")]:
        monkeypatch.delenv(name, raising=False)


@pytest.fixture
def git():
    """Run a git command in a repo. The one subprocess call site in the test tree besides the hooks."""
    binary = shutil.which("git")
    assert binary, "git is required to test the hooks"

    def run(repo: Path, *args: str) -> str:
        # S603: fixed literal argv from the tests themselves, resolved binary, no shell.
        result = subprocess.run((binary, *args), cwd=repo, check=True, capture_output=True, text=True)  # noqa: S603
        return result.stdout.strip()

    return run


@pytest.fixture
def repo(tmp_path, git):
    """A `feature` branch with a remote, an upstream, and nothing left to push.

    Everything-pushed is the interesting baseline: each test that wants an unpushed commit adds
    one, so a test about the "push me" nudge cannot pass by accident on a repo that never had an
    upstream in the first place.
    """
    remote = tmp_path / "remote.git"
    git(tmp_path, "init", "-q", "--bare", str(remote))

    root = tmp_path / "repo"
    root.mkdir()
    git(root, "init", "-q", "-b", "main")
    git(root, "config", "user.email", "t@example.com")
    git(root, "config", "user.name", "t")
    git(root, "remote", "add", "origin", str(remote))
    (root / "base.py").write_text("x = 1\n")
    git(root, "add", "base.py")
    git(root, "commit", "-qm", "base")
    git(root, "push", "-q", "-u", "origin", "main")
    git(root, "remote", "set-head", "origin", "main")  # `origin/HEAD`: the trunk branches are measured against
    git(root, "checkout", "-q", "-b", "feature")
    (root / "feature.py").write_text("y = 2\n")
    git(root, "add", "feature.py")
    git(root, "commit", "-qm", "feature work")
    git(root, "push", "-q", "-u", "origin", "feature")
    return root


@pytest.fixture
def commit(git):
    """Add one more commit on top, i.e. make the branch ahead of its upstream."""

    def add(repo: Path, name: str = "more.py") -> None:
        (repo / name).write_text("z = 3\n")
        git(repo, "add", name)
        git(repo, "commit", "-qm", f"add {name}")

    return add


@pytest.fixture
def run_hook(tmp_path):
    """Run one of the hooks the way Claude Code does, and return its parsed output (None if silent).

    A stub `gh` is prepended to PATH so no test can reach GitHub, and `HOME` points at tmp_path
    so nothing a hook might write lands in the real `~/.claude`.
    """
    home = tmp_path / "home"
    home.mkdir()
    bindir = tmp_path / "bin"
    bindir.mkdir()
    stub = bindir / "gh"
    stub.write_text(GH_STUB)
    stub.chmod(0o755)

    def run(script: str, payload: dict[str, object], cwd: Path, gh_mode: str = "none") -> HookOutput | None:
        env = {
            **os.environ,
            "HOME": str(home),
            "PATH": f"{bindir}{os.pathsep}{os.environ['PATH']}",
            "FAKE_GH_MODE": gh_mode,
            "PR_FLOW_GH_TIMEOUT_S": "1",  # the `hang` mode must be cheap to assert
        }
        # S603: argv is this interpreter plus the hook under test; no shell, no user input.
        result = subprocess.run(  # noqa: S603
            (sys.executable, str(HOOKS_DIR / script)),
            input=json.dumps({**payload, "cwd": str(cwd)}),
            capture_output=True,
            text=True,
            env=env,
            check=True,
        )
        return json.loads(result.stdout) if result.stdout.strip() else None

    return run


@pytest.fixture
def bash(run_hook):
    """Run `nudge_after_git.py` over a Bash command that already ran."""

    def run(command: str, cwd: Path, gh_mode: str = "none") -> HookOutput | None:
        payload: dict[str, object] = {
            "hook_event_name": "PostToolUse",
            "tool_name": "Bash",
            "tool_input": {"command": command},
        }
        return run_hook("nudge_after_git.py", payload, cwd, gh_mode)

    return run
