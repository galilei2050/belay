"""Test fixtures for usable-ui.

Everything here exercises the hook at its real boundary: the script Claude Code actually
runs, fed a JSON payload on stdin. Nothing imports the hook module, so the tests are free to
be wrong about its internals and still right about what the agent receives.
"""

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import TypedDict

import pytest

HOOK = Path(__file__).parent.parent / "hooks" / "usable_ui_hook.py"


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
    land in belay itself.
    """
    for name in [key for key in os.environ if key.startswith("GIT_")]:
        monkeypatch.delenv(name, raising=False)


@pytest.fixture
def git():
    """Run a git command in a repo. The one subprocess call site in the tests besides the hook."""
    binary = shutil.which("git")
    assert binary, "git is required to test the hook"

    def run(repo: Path, *args: str) -> None:
        # S603: fixed literal argv from the tests themselves, resolved binary, no shell.
        subprocess.run((binary, *args), cwd=repo, check=True, capture_output=True)  # noqa: S603

    return run


@pytest.fixture
def repo(tmp_path, git):
    """A git repo with one commit on HEAD and nothing staged yet."""
    root = tmp_path / "repo"
    root.mkdir()
    git(root, "init", "-q")
    git(root, "config", "user.email", "t@example.com")
    git(root, "config", "user.name", "t")
    (root / "base.py").write_text("x = 1\n")
    git(root, "add", "base.py")
    git(root, "commit", "-qm", "base")
    return root


@pytest.fixture
def stage(git):
    """Write a file into the repo and stage it, so a commit has something to review."""

    def add(repo: Path, name: str, content: str = "<div>hi</div>\n") -> Path:
        path = repo / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content)
        git(repo, "add", name)
        return path

    return add


class Hook:
    """The hook as Claude Code runs it, with a `HOME` of its own so the dedupe state is per-test."""

    def __init__(self, home: Path) -> None:
        self._env = {**os.environ, "HOME": str(home)}

    def bash(self, command: str, cwd: Path, session_id: str = "s1") -> HookOutput | None:
        return self._run("Bash", {"command": command}, str(cwd), session_id)

    def edit(self, path: str, tool_name: str = "Write", session_id: str = "s1") -> HookOutput | None:
        key = "notebook_path" if tool_name == "NotebookEdit" else "file_path"
        return self._run(tool_name, {key: path}, ".", session_id)

    def _run(self, tool_name: str, tool_input: dict[str, str], cwd: str, session_id: str) -> HookOutput | None:
        payload = json.dumps({"tool_name": tool_name, "tool_input": tool_input, "cwd": cwd, "session_id": session_id})
        # S603: argv is this interpreter plus the hook under test; no shell, no user input.
        result = subprocess.run(  # noqa: S603
            (sys.executable, str(HOOK)),
            input=payload,
            capture_output=True,
            text=True,
            env=self._env,
            check=True,
        )
        return json.loads(result.stdout) if result.stdout.strip() else None


@pytest.fixture
def hook(tmp_path):
    """One hook per test, sharing dedupe state across calls the way a real session does."""
    home = tmp_path / "home"
    home.mkdir()
    return Hook(home)
