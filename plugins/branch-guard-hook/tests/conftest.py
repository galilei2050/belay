"""Test fixtures for branch-guard-hook.

Puts the hook's source dir on sys.path so `import branch_guard_hook` works, and builds a tmp
checkout (real `.git/HEAD`, `src/`, `.scratch/`, `.claude/`) so both branch detection and path
classification are deterministic.
"""

import sys
from pathlib import Path

import pytest

_HOOKS_DIR = str(Path(__file__).parent.parent / "hooks")
if _HOOKS_DIR not in sys.path:
    sys.path.insert(0, _HOOKS_DIR)


@pytest.fixture(autouse=True)
def fix_project_dir(tmp_path, monkeypatch):
    """Pin PROJECT_DIR to a tmp checkout sitting on `main`."""
    import branch_guard_hook

    project = tmp_path / "project"
    (project / "src").mkdir(parents=True)
    (project / ".scratch").mkdir()
    (project / ".claude").mkdir()
    (project / ".git").mkdir()
    (project / ".git" / "HEAD").write_text("ref: refs/heads/main\n", encoding="utf-8")

    monkeypatch.setattr(branch_guard_hook, "PROJECT_DIR", str(project.resolve()))
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(project))
    return project


@pytest.fixture
def on_branch(fix_project_dir):
    """Point the tmp checkout's HEAD at `name`."""

    def _set(name: str) -> Path:
        (fix_project_dir / ".git" / "HEAD").write_text(f"ref: refs/heads/{name}\n", encoding="utf-8")
        return fix_project_dir

    return _set
