"""Test fixtures for comment-guard-hook.

Puts the hook's source dir on sys.path so `import comment_guard_hook` works.
"""

import sys
from pathlib import Path

_HOOKS_DIR = str(Path(__file__).parent.parent / "hooks")
if _HOOKS_DIR not in sys.path:
    sys.path.insert(0, _HOOKS_DIR)
