"""anon-lint: ban anonymous tuple/dict in function/class type annotations.

Run as:
    python -m anon_lint <files_or_dirs...> [--recursive]

Tests live in tests/test_anon_lint.py and run under `uv run pytest`.
"""

from __future__ import annotations

import argparse
import ast
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Iterable, Iterator

DICT_LIKE = {"dict", "Dict", "Mapping", "MutableMapping"}
TUPLE_LIKE = {"tuple", "Tuple"}
ANY_NAMES = {"Any"}

# A tuple annotation needs at least this many element types before we flag it
# as anonymous (single-element tuples are usually `tuple[X]` containers).
_TUPLE_MIN_FLAG_ELEMENTS = 2
# A dict annotation must have exactly key + value to be a candidate.
_DICT_SLICE_ELEMENTS = 2

NOQA_RE = re.compile(r"#\s*noqa\s*:\s*([A-Za-z0-9, ]+)")


@dataclass(frozen=True)
class Finding:
    """One anonymous-annotation violation: file/line plus code and message."""

    path: Path
    line: int
    col: int
    code: str
    message: str


def _name_of(node: ast.expr) -> str | None:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return node.attr
    return None


def _is_ellipsis(node: ast.expr) -> bool:
    return isinstance(node, ast.Constant) and node.value is Ellipsis


def _is_any(node: ast.expr) -> bool:
    return _name_of(node) in ANY_NAMES


def _is_bare_dict_like(node: ast.expr) -> bool:
    return _name_of(node) in DICT_LIKE


def _is_dict_like_anywhere(node: ast.expr) -> bool:
    if _is_bare_dict_like(node):
        return True
    return isinstance(node, ast.Subscript) and _is_bare_dict_like(node.value)


def _slice_elts(slice_node: ast.expr) -> list[ast.expr]:
    if isinstance(slice_node, ast.Tuple):
        return list(slice_node.elts)
    return [slice_node]


def _src(node: ast.expr) -> str:
    try:
        return ast.unparse(node)
    except Exception:  # noqa: BLE001 — ast.unparse can fail on edge cases; fall back to "?"
        return "?"


def _is_typealias(annotation: ast.expr | None) -> bool:
    return annotation is not None and _name_of(annotation) == "TypeAlias"


def _subscript_base(node: ast.expr) -> str | None:
    return _name_of(node.value) if isinstance(node, ast.Subscript) else None


class _Checker:
    def __init__(self, source_lines: list[str], path: Path) -> None:
        self.source_lines = source_lines
        self.path = path
        self.findings: list[Finding] = []

    def _suppressed(self, line: int, code: str) -> bool:
        if line < 1 or line > len(self.source_lines):
            return False
        match = NOQA_RE.search(self.source_lines[line - 1])
        if not match:
            return False
        codes = {c.strip().upper() for c in match.group(1).split(",")}
        return code in codes

    def _report(self, anchor: ast.expr, offender: ast.expr, code: str, message: str) -> None:
        line = getattr(anchor, "lineno", 1)
        col = getattr(anchor, "col_offset", 0)
        if self._suppressed(line, code):
            return
        self.findings.append(Finding(self.path, line, col, code, f"{_src(offender)} — {message}"))

    def check_annotation(self, node: ast.expr | None) -> None:
        """Walk a single function/class annotation looking for anonymous tuple/dict."""
        if node is None:
            return
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            try:
                inner = ast.parse(node.value, mode="eval").body
            except SyntaxError:
                return
            self._walk(inner, report_node=node)
            return
        self._walk(node, report_node=None)

    def _walk(self, node: ast.expr, *, report_node: ast.expr | None) -> None:
        if _is_bare_dict_like(node):
            anchor = report_node if report_node is not None else node
            self._report(anchor, node, "ANON002", "use @dataclass/TypedDict/pydantic")
            return
        if isinstance(node, ast.Subscript):
            self._walk_subscript(node, report_node=report_node)
            return
        for child in ast.iter_child_nodes(node):
            if isinstance(child, ast.expr):
                self._walk(child, report_node=report_node)

    def _walk_subscript(self, node: ast.Subscript, *, report_node: ast.expr | None) -> None:
        base = _subscript_base(node)
        if base in TUPLE_LIKE:
            self._check_tuple(node, report_node=report_node)
        elif base in DICT_LIKE:
            self._check_dict(node, report_node=report_node)
        for elt in _slice_elts(node.slice):
            self._walk(elt, report_node=report_node)

    def _check_tuple(self, sub: ast.Subscript, *, report_node: ast.expr | None) -> None:
        anchor = report_node if report_node is not None else sub
        slice_node = sub.slice
        if not isinstance(slice_node, ast.Tuple):
            return
        elts = slice_node.elts
        if len(elts) < _TUPLE_MIN_FLAG_ELEMENTS:
            return
        if _is_ellipsis(elts[-1]):
            return
        self._report(anchor, sub, "ANON001", "use @dataclass/NamedTuple")

    def _check_dict(self, sub: ast.Subscript, *, report_node: ast.expr | None) -> None:
        anchor = report_node if report_node is not None else sub
        elts = _slice_elts(sub.slice)
        if len(elts) != _DICT_SLICE_ELEMENTS:
            return
        value = elts[1]
        if _is_any(value) or _is_dict_like_anywhere(value):
            self._report(anchor, sub, "ANON002", "use @dataclass/TypedDict/pydantic")


def _check_function(fn: ast.FunctionDef | ast.AsyncFunctionDef, checker: _Checker) -> None:
    a = fn.args
    for arg in (*a.posonlyargs, *a.args, *a.kwonlyargs):
        checker.check_annotation(arg.annotation)
    if a.vararg is not None:
        checker.check_annotation(a.vararg.annotation)
    if a.kwarg is not None:
        checker.check_annotation(a.kwarg.annotation)
    checker.check_annotation(fn.returns)


def lint_source(source: str, path: Path) -> list[Finding]:
    """Run the checker on a single source string; return all findings (may be empty)."""
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return []
    checker = _Checker(source.splitlines(), path)
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
            _check_function(node, checker)
        elif isinstance(node, ast.ClassDef):
            for stmt in node.body:
                if isinstance(stmt, ast.AnnAssign) and not _is_typealias(stmt.annotation):
                    checker.check_annotation(stmt.annotation)
    checker.findings.sort(key=lambda f: (str(f.path), f.line, f.col, f.code))
    return checker.findings


def lint_file(path: Path) -> list[Finding]:
    """Read a file from disk and lint it; silently skips unreadable paths."""
    try:
        source = path.read_text(encoding="utf-8")
    except OSError:
        return []
    return lint_source(source, path)


def iter_files(targets: Iterable[Path], *, recursive: bool) -> Iterator[Path]:
    """Expand a list of files/dirs into the actual `.py` files to lint."""
    for target in targets:
        if target.is_file():
            yield target
        elif target.is_dir():
            pattern = "**/*.py" if recursive else "*.py"
            yield from sorted(target.glob(pattern))


def format_finding(f: Finding) -> str:
    """Render a finding as the canonical `path:line:col: CODE message` form."""
    return f"{f.path}:{f.line}:{f.col}: {f.code} {f.message}"


def main(argv: list[str] | None = None) -> int:
    """CLI entry point; returns 0 when no findings, 1 otherwise."""
    parser = argparse.ArgumentParser(prog="anon-lint")
    parser.add_argument("paths", nargs="+", type=Path)
    parser.add_argument("--recursive", "-r", action="store_true")
    args = parser.parse_args(argv)

    findings: list[Finding] = []
    for f in iter_files(args.paths, recursive=args.recursive):
        findings.extend(lint_file(f))
    for fnd in findings:
        sys.stdout.write(format_finding(fnd) + "\n")
    return 1 if findings else 0


if __name__ == "__main__":
    sys.exit(main())
