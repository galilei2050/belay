"""Test fixtures for review-panel.

Everything here exists to exercise the hook at its real boundary: the script Claude Code
actually runs, fed a JSON payload on stdin. Nothing imports the hook module, so the tests
are free to be wrong about its internals and still right about its behavior.
"""

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import TypedDict

import pytest

HOOK = Path(__file__).parent.parent / "hooks" / "review_panel_hook.py"


def many_lines(marker: str) -> str:
    """A file body well over the hook's dispatch threshold, so the change is one the panel takes."""
    return "".join(f"{marker}{n} = {n}\n" for n in range(80))


@pytest.fixture
def big_file():
    """`many_lines` as a fixture — every plugin's `conftest.py` is the same top-level module name
    to a type checker, so a test that reaches one by `from conftest import ...` resolves to
    whichever plugin sorts first. Fixtures are looked up by pytest per directory instead.
    """
    return many_lines


class HookSpecificOutput(TypedDict):
    """The payload Claude Code reads back from a PreToolUse hook."""

    hookEventName: str
    additionalContext: str


class HookOutput(TypedDict):
    """What the hook writes to stdout. Absent `permissionDecision` is the point — see the tests."""

    hookSpecificOutput: HookSpecificOutput


@pytest.fixture(autouse=True)
def clean_git_env(monkeypatch):
    """Drop inherited `GIT_*` vars so the fixture repo is not hijacked by an outer git process.

    `make ci` runs with a clean environment, but the pre-push hook runs these tests *inside*
    git, which exports GIT_DIR and GIT_INDEX_FILE. Without this the fixture's commits would
    land in belay itself and `rev-parse --show-toplevel` would succeed outside any repo.
    """
    for name in [key for key in os.environ if key.startswith("GIT_")]:
        monkeypatch.delenv(name, raising=False)


@pytest.fixture
def git():
    """Run a git command in a repo. The one subprocess call site in the test tree besides the hook."""
    binary = shutil.which("git")
    assert binary, "git is required to test the hook"

    def run(repo: Path, *args: str) -> None:
        # S603: fixed literal argv from the tests themselves, resolved binary, no shell.
        subprocess.run((binary, *args), cwd=repo, check=True, capture_output=True)  # noqa: S603

    return run


@pytest.fixture
def repo(tmp_path, git):
    """A git repo with one commit on HEAD and a substantial `staged.py` staged on top of it.

    Staged big deliberately: the hook ignores a diff under `MIN_CHANGED_LINES`, so a one-line
    fixture would make every dispatch test pass for the wrong reason.
    """
    root = tmp_path / "repo"
    root.mkdir()
    git(root, "init", "-q")
    git(root, "config", "user.email", "t@example.com")
    git(root, "config", "user.name", "t")
    (root / "base.py").write_text("x = 1\n")
    git(root, "add", "base.py")
    git(root, "commit", "-qm", "base")
    (root / "staged.py").write_text(many_lines("y"))
    git(root, "add", "staged.py")
    return root


@pytest.fixture
def run_hook(tmp_path):
    """Run the hook the way Claude Code does, and return its parsed output (None if silent).

    `HOME` points at tmp_path so the dedupe state the hook writes never touches the real
    `~/.claude`; each test therefore starts with the panel having seen nothing.
    """
    home = tmp_path / "home"
    home.mkdir()
    env = {**os.environ, "HOME": str(home)}

    def run(command: str, cwd: Path, tool_name: str = "Bash") -> HookOutput | None:
        payload = json.dumps({"tool_name": tool_name, "tool_input": {"command": command}, "cwd": str(cwd)})
        # S603: argv is this interpreter plus the hook under test; no shell, no user input.
        result = subprocess.run(  # noqa: S603
            (sys.executable, str(HOOK)),
            input=payload,
            capture_output=True,
            text=True,
            env=env,
            check=True,
        )
        return json.loads(result.stdout) if result.stdout.strip() else None

    return run
