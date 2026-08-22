"""Test fixtures for delegation-hook.

Puts the hook's source dir on sys.path so `import delegation_hook` works, and redirects the counter
store into a tmp dir so a test run neither reads nor writes the real `~/.claude/state`.
"""

import sys
from pathlib import Path

import pytest

_HOOKS_DIR = str(Path(__file__).parent.parent / "hooks")
if _HOOKS_DIR not in sys.path:
    sys.path.insert(0, _HOOKS_DIR)


@pytest.fixture(autouse=True)
def state_dir(tmp_path, monkeypatch):
    """Pin STATE_DIR to a tmp dir, so each test starts with every agent's counter at zero."""
    import delegation_hook

    store = tmp_path / "delegation-hook"
    monkeypatch.setattr(delegation_hook, "STATE_DIR", store)
    return store
