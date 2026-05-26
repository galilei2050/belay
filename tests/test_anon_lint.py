"""Tests for anon_lint.py — the anonymous-tuple/dict checker.

Run via the standard `uv run pytest` invocation; lives at the repo root so
the project-wide `make test` picks it up alongside the per-plugin suites.
"""

from __future__ import annotations

import sys
import textwrap
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from anon_lint import lint_source  # noqa: E402

_FIRES = [
    ("def f() -> tuple[str, int]: ...", {"ANON001"}),
    ("def f(x: tuple[str, int, bool]) -> None: ...", {"ANON001"}),
    ("def f() -> dict[str, Any]: ...", {"ANON002"}),
    ("def f() -> dict: ...", {"ANON002"}),
    ("def f() -> list[tuple[str, int]]: ...", {"ANON001"}),
    ("class C:\n    data: dict[str, dict[str, int]]\n", {"ANON002"}),
    ('def f(x: "tuple[str, int]") -> None: ...', {"ANON001"}),
    ("def f(*args: tuple[str, int]) -> None: ...", {"ANON001"}),
    ("def f(**kw: dict[str, Any]) -> None: ...", {"ANON002"}),
    ("from typing import Tuple\ndef f() -> Tuple[str, int]: ...", {"ANON001"}),
    ("from typing import Dict, Any\ndef f() -> Dict[str, Any]: ...", {"ANON002"}),
    ("def f() -> Callable[[int], dict[str, Any]]: ...", {"ANON002"}),
]

_CLEAN = [
    "def f() -> tuple[int, ...]: ...",
    "def f() -> tuple[int]: ...",
    "def f() -> dict[str, int]: ...",
    "def f() -> dict[str, MyClass]: ...",
    "def f() -> tuple[str, int]: ...  # noqa: ANON001",
    "def f() -> dict[str, Any]: ...  # noqa: ANON002",
    "from typing import TypeAlias\nResult: TypeAlias = tuple[str, int]\n",
    "def f() -> None:\n    x: tuple[str, int] = ('a', 1)\n",
    "x: tuple[str, int] = ('a', 1)\n",
    "def f() -> None: ...",
    "class C:\n    x: int\n    y: str\n",
]


@pytest.mark.parametrize(("src", "expected"), _FIRES)
def test_fires(src: str, expected: set[str]) -> None:
    findings = lint_source(textwrap.dedent(src), Path("x.py"))
    assert findings, f"expected {expected}, got nothing"
    codes = {f.code for f in findings}
    assert expected.issubset(codes), f"expected {expected}, got {codes}"


@pytest.mark.parametrize("src", _CLEAN)
def test_clean(src: str) -> None:
    findings = lint_source(textwrap.dedent(src), Path("x.py"))
    assert findings == [], [f"{f.code}: {f.message}" for f in findings]


def test_noqa_multiple_codes() -> None:
    src = "def f() -> tuple[str, int]: ...  # noqa: ANON001, ANON002"
    assert lint_source(src, Path("x.py")) == []


def test_class_attr_fires() -> None:
    src = "class C:\n    data: tuple[str, int]\n"
    findings = lint_source(src, Path("x.py"))
    assert [f.code for f in findings] == ["ANON001"]


def test_nested_tuple_in_dict_value() -> None:
    src = "def f() -> dict[str, tuple[int, int]]: ...\n"
    findings = lint_source(src, Path("x.py"))
    assert "ANON001" in {f.code for f in findings}
