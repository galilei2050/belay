"""Test fixtures for fs-acl-hook.

Puts the hook's source dir on sys.path so `import fs_acl_hook` works, and pins PROJECT_DIR
to a known tmp project (with `src/` and `.scratch/`) so path classification is deterministic.
"""

import sys
from pathlib import Path

import pytest

_HOOKS_DIR = str(Path(__file__).parent.parent / "hooks")
if _HOOKS_DIR not in sys.path:
    sys.path.insert(0, _HOOKS_DIR)


@pytest.fixture(autouse=True)
def fix_project_dir(tmp_path, monkeypatch):
    """Pin PROJECT_DIR to a tmp project so in/out-of-project checks are deterministic."""
    import fs_acl_hook

    project = tmp_path / "project"
    (project / "src").mkdir(parents=True)
    (project / ".scratch").mkdir()

    monkeypatch.setattr(fs_acl_hook, "PROJECT_DIR", str(project.resolve()))
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(project))
    return project
