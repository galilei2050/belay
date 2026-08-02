"""Test fixtures for review-panel-hook.

Puts the hook's source dir on sys.path so `import review_panel_hook` works, redirects the
dedupe state file into tmp_path so tests never touch the real `~/.claude`, and builds a
throwaway git repo with staged content for the digest / end-to-end paths.
"""

import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

_HOOKS_DIR = str(Path(__file__).parent.parent / "hooks")
if _HOOKS_DIR not in sys.path:
    sys.path.insert(0, _HOOKS_DIR)


@pytest.fixture(autouse=True)
def clean_git_env(monkeypatch):
    """Drop inherited `GIT_*` vars so the fixture repo is not hijacked by an outer git process.

    `make ci` runs with a clean environment, but the pre-push hook runs these tests *inside*
    git, which exports GIT_DIR and GIT_INDEX_FILE. Without this the fixture's commits would
    land in belay itself and `rev-parse --show-toplevel` would succeed outside any repo.
    """
    for name in [key for key in os.environ if key.startswith("GIT_")]:
        monkeypatch.delenv(name, raising=False)


@pytest.fixture(autouse=True)
def state_file(tmp_path, monkeypatch):
    """Point the dedupe state at tmp_path so the real ~/.claude is never written."""
    import review_panel_hook

    path = tmp_path / "state" / "reviewed.json"
    monkeypatch.setattr(review_panel_hook, "STATE_PATH", path)
    return path


@pytest.fixture
def git():
    """Run a git command in a repo. The one subprocess call site in the test tree."""
    binary = shutil.which("git")
    assert binary, "git is required to test the hook"

    def run(repo: Path, *args: str) -> None:
        # S603: fixed literal argv from the tests themselves, resolved binary, no shell.
        subprocess.run((binary, *args), cwd=repo, check=True, capture_output=True)  # noqa: S603

    return run


@pytest.fixture
def repo(tmp_path, git):
    """A git repo with one commit on HEAD and `staged.py` staged on top of it."""
    root = tmp_path / "repo"
    root.mkdir()
    git(root, "init", "-q")
    git(root, "config", "user.email", "t@example.com")
    git(root, "config", "user.name", "t")
    (root / "base.py").write_text("x = 1\n")
    git(root, "add", "base.py")
    git(root, "commit", "-qm", "base")
    (root / "staged.py").write_text("y = 2\n")
    git(root, "add", "staged.py")
    return root
