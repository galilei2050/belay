"""Test fixtures for acl-hook.

Puts the hook's source directory on sys.path so `import acl_hook` works, and
sets PROJECT_DIR to a known location so path-inside-project checks are
predictable across machines.
"""

import logging
import sys
from pathlib import Path

import pytest

_HOOKS_DIR = str(Path(__file__).parent.parent / "hooks")
if _HOOKS_DIR not in sys.path:
    sys.path.insert(0, _HOOKS_DIR)


@pytest.fixture(autouse=True)
def fix_project_dir(tmp_path, monkeypatch):
    """Pin PROJECT_DIR to a tmp dir so rm/rmdir path tests are deterministic.

    The real plugin reads CLAUDE_PROJECT_DIR from the env at import time and
    bakes it into a module-level constant. For tests we patch the constant
    directly — and also create the subdirs the path tests reference so realpath
    resolution doesn't surprise us.
    """
    import acl_hook

    project = tmp_path / "project"
    project.mkdir()
    for sub in ("app", "tests", "infrastructure", "web", "tmp"):
        (project / sub).mkdir()

    monkeypatch.setattr(acl_hook, "PROJECT_DIR", str(project))
    monkeypatch.setattr(acl_hook, "_ACL_CACHE", None)
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(project))
    return project


@pytest.fixture
def logger():
    log = logging.getLogger("test_acl_hook")
    if not log.handlers:
        log.addHandler(logging.NullHandler())
    return log
