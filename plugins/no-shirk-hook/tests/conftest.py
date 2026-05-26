"""Test fixtures for no-shirk-hook."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

_HOOKS_DIR = str(Path(__file__).parent.parent / "hooks")
if _HOOKS_DIR not in sys.path:
    sys.path.insert(0, _HOOKS_DIR)


@pytest.fixture
def write_transcript(tmp_path):
    """Return a function that writes a JSONL transcript and returns its path.

    Accepts a list of (role, text) tuples. `text` may be a string (single text block)
    or a list of content blocks (dicts with type/text/tool_use etc.).
    """

    def _write(messages: list[tuple[str, object]]) -> str:
        path = tmp_path / "transcript.jsonl"
        with path.open("w", encoding="utf-8") as f:
            for role, content in messages:
                if isinstance(content, str):
                    msg = {"role": role, "content": [{"type": "text", "text": content}]}
                else:
                    msg = {"role": role, "content": content}
                f.write(json.dumps({"message": msg}) + "\n")
        return str(path)

    return _write
