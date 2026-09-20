"""Test fixtures for agent-knowledge.

The hook is exercised at its real boundary: the script Claude Code runs, fed a PostToolUse payload
on stdin (or paths on argv for the audit), against real files in a real git repo.
"""

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import NamedTuple

import pytest

HOOK = Path(__file__).parent.parent / "hooks" / "claude_md.py"


class AuditResult(NamedTuple):
    status: int
    stdout: str


@pytest.fixture(autouse=True)
def clean_git_env(monkeypatch):
    """Drop inherited `GIT_*` vars — the pre-push hook runs these tests inside git itself."""
    for name in [key for key in os.environ if key.startswith("GIT_")]:
        monkeypatch.delenv(name, raising=False)


@pytest.fixture
def git():
    binary = shutil.which("git")
    assert binary, "git is required to test the audit"

    def run(repo: Path, *args: str) -> str:
        # S603: fixed literal argv from the tests themselves, resolved binary, no shell.
        return subprocess.run((binary, *args), cwd=repo, check=True, capture_output=True, text=True).stdout.strip()  # noqa: S603

    return run


@pytest.fixture
def repo(tmp_path, git):
    """A git repo with one tracked source dir, so path checks have a real tree to resolve against."""
    root = tmp_path / "repo"
    (root / "src").mkdir(parents=True)
    (root / "src" / "app.py").write_text("x = 1\n")
    git(root, "init", "-q", "-b", "main")
    git(root, "config", "user.email", "t@example.com")
    git(root, "config", "user.name", "t")
    git(root, "add", ".")
    git(root, "commit", "-qm", "base")
    return root


@pytest.fixture
def after_write():
    """Run the hook as PostToolUse over a file that was just written; returns its parsed output."""

    def run(path: Path, tool_name: str = "Write", path_key: str = "file_path") -> dict[str, str] | None:
        payload = {"hook_event_name": "PostToolUse", "tool_name": tool_name, "tool_input": {path_key: str(path)}}
        # S603: argv is this interpreter plus the hook under test; no shell, no user input.
        result = subprocess.run(  # noqa: S603
            (sys.executable, str(HOOK)), input=json.dumps(payload), capture_output=True, text=True, check=True
        )
        return json.loads(result.stdout) if result.stdout.strip() else None

    return run


@pytest.fixture
def run_audit():
    """Run the audit CLI over files or directories."""

    def run(*paths: Path) -> AuditResult:
        # S603: argv is this interpreter plus the hook under test; no shell, no user input.
        result = subprocess.run(  # noqa: S603
            (sys.executable, str(HOOK), *map(str, paths)), capture_output=True, text=True, check=False
        )
        return AuditResult(result.returncode, result.stdout)

    return run
