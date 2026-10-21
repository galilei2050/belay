#!/usr/bin/env python3
"""Docstring-length gate for Claude Code file edits — three lines, then stop.

A model asked to write a contract writes it in the docstrings, and an essay above a function reads
as finished work long enough to survive review. No rule about *what* a comment may say can be
enforced without judging meaning, which a hook cannot do. A line count can.

Only `deny` is ever emitted, and only for prose this very edit is answerable for.
"""

from __future__ import annotations

import ast
import difflib
import io
import json
import re
import sys
import tokenize
from pathlib import Path
from typing import NamedTuple, NotRequired, TypedDict

MAX_LINES = 3

GUARDED_SUFFIX = ".py"

# The matcher in hooks.json must never name a tool this hook ignores, and vice versa: a tool that
# reached `post_edit_source` unrecognised would be replayed as an `Edit` and die on a missing key.
EDIT_TOOLS = frozenset({"Write", "Edit", "MultiEdit"})

# Machine-readable header lines, which are not prose and have no shorter legal form — a file needs
# its shebang, its coding cookie, its licence tag and its tool pragmas, and four of them in a row
# is a normal header, not an essay. This recognises fixed directive syntax, not meaning.
_DIRECTIVE = re.compile(
    r"^#!"
    r"|^#\s*-\*-"
    r"|^#\s*(?:SPDX-[\w-]+|Copyright)\b"
    r"|^#\s*(?:ruff|mypy|flake8|pylint|isort|pyright|type|coding|pragma)\s*:"
    r"|^#\s*noqa\b",
    re.IGNORECASE,
)

_REASON = (
    "{count} docstring(s)/comment(s) in this {tool} run past {limit} lines:\n\n{listed}\n\n"
    "A docstring describes the thing it sits on, and nothing else: how to call it and what you "
    "get back. The test, from the Google Python Style Guide — it should give enough information "
    "to write a call without reading the function's code. The same guide says the Args/Returns/"
    "Raises sections may be omitted \"in cases where the function's name and signature are "
    'informative enough that it can be aptly described using a one-line docstring". Making that '
    "true of your function is the work here; {limit} lines is what it costs when it is.\n\n"
    "It does not fit in {limit} lines? In this order:\n"
    "  1. Name it better. `_company_wide_shop` needed a paragraph; `shop_for_ad_call` needs a "
    "line. A precise name documents for free and cannot go stale.\n"
    "  2. Say it shorter. Most of an essay is the code restated, the history of how it got here, "
    "and hedging. None of that is the contract.\n"
    "  3. Refactor. A function that still needs more than {limit} lines to explain is doing more "
    "than one thing — split it, and each part explains itself in one line.\n\n"
    "And wherever it ends up, this never belongs in a docstring:\n"
    "  - rules, decisions and open questions that outlive the function -> CLAUDE.md or the design doc\n"
    "  - why one non-obvious line is written that way -> a short comment on that line\n"
    "  - why this change, and what it replaced -> the commit message"
)

_LINE_REF = re.compile(r"\w+\.py:\d+")

_REF_REASON = (
    "{count} line reference(s) in a comment or docstring in this {tool}:\n\n{listed}\n\n"
    "A line number goes stale on the next commit, and pointing at another file's lines means the "
    "text describes the neighbour, not the thing it sits on. Say what this code is; the "
    "cross-reference belongs in the commit message."
)


class Replacement(TypedDict):
    """One search-and-replace, as `Edit` sends it and as each entry of `MultiEdit`'s list does."""

    old_string: str
    new_string: str
    replace_all: NotRequired[bool]


class Finding(NamedTuple):
    """One docstring or comment block that runs past the cap."""

    line: int
    what: str
    length: int


class LineRef(NamedTuple):
    """One path-with-line-number reference found in a comment or docstring."""

    line: int
    what: str
    ref: str


class Verdict(NamedTuple):
    """Everything one edit is denied for: blocks over the cap, and line references."""

    findings: list[Finding]
    refs: list[LineRef]


class Docstring(NamedTuple):
    """A docstring with the inclusive source span of its string literal."""

    node: ast.Module | ast.ClassDef | ast.FunctionDef | ast.AsyncFunctionDef
    start: int
    end: int
    text: str


class CommentLine(NamedTuple):
    """One own-line `#` comment: the source line it sits on, and the whole token including `#`."""

    line: int
    text: str


def _touched(start: int, end: int, changed: frozenset[int]) -> bool:
    """True when this edit is answerable for any line of the inclusive span `start`..`end`.

    The whole span, not just its first line: an essay usually grows by appending to a docstring
    whose opening quote was written long ago and has not moved.
    """
    return not changed.isdisjoint(range(start, end + 1))


def _prose_lines(text: str) -> int:
    return sum(1 for line in text.splitlines() if line.strip())


def _docstrings(tree: ast.AST, changed: frozenset[int]) -> list[Docstring]:
    """Every module, class and function docstring whose span this edit touched."""
    out = []
    for node in ast.walk(tree):
        if not isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        text = ast.get_docstring(node, clean=False)
        if text is None or not isinstance(first := node.body[0], ast.Expr):
            continue
        start, end = first.value.lineno, first.value.end_lineno or first.value.lineno
        if _touched(start, end, changed):
            out.append(Docstring(node, start, end, text))
    return out


def _label(doc: Docstring) -> str:
    return "module docstring" if isinstance(doc.node, ast.Module) else f"docstring of `{doc.node.name}`"


def _docstring_findings(docs: list[Docstring]) -> list[Finding]:
    """Function and class docstrings over the cap. Module docstrings are deliberately absent.

    One orientation paragraph per file is the cheapest context a reader gets, and unlike a method's
    it sits on no signature that could have carried the information instead.
    """
    capped = (d for d in docs if not isinstance(d.node, ast.Module))
    return [Finding(d.start, _label(d), _prose_lines(d.text)) for d in capped if _prose_lines(d.text) > MAX_LINES]


def _line_refs(docs: list[Docstring], source: str, changed: frozenset[int]) -> list[LineRef]:
    """Every `_LINE_REF` match in a touched docstring, module ones included, or touched comment.

    Trailing comments count too: this is about what the text points at, not how long it runs.
    """
    refs = [
        LineRef(doc.start + offset, _label(doc), ref)
        for doc in docs
        for offset, line in enumerate(doc.text.splitlines())
        for ref in _LINE_REF.findall(line)
    ]
    tokens = tokenize.generate_tokens(io.StringIO(source).readline)
    comments = (t for t in tokens if t.type == tokenize.COMMENT and t.start[0] in changed)
    return refs + [LineRef(t.start[0], "comment", ref) for t in comments for ref in _LINE_REF.findall(t.string)]


def own_line_comments(source: str) -> list[CommentLine]:
    """Every `#` comment that is alone on its line, directives excluded.

    A trailing comment annotates its own line of code rather than forming a block, and a directive
    is machine input with no shorter legal form.
    """
    tokens = tokenize.generate_tokens(io.StringIO(source).readline)
    alone = (t for t in tokens if t.type == tokenize.COMMENT and t.line.lstrip().startswith("#"))
    return [CommentLine(t.start[0], t.string) for t in alone if not _DIRECTIVE.match(t.string)]


def _comment_findings(source: str, changed: frozenset[int]) -> list[Finding]:
    """Runs of consecutive own-line comments, measured exactly as a docstring is.

    Without this the cap is one keystroke away from being routed around: the essay moves from
    inside the quotes to a `#` block above them. A bare `#` is the blank line of that form.
    """
    runs: list[list[CommentLine]] = []
    for comment in own_line_comments(source):
        if runs and comment.line == runs[-1][-1].line + 1:
            runs[-1].append(comment)
        else:
            runs.append([comment])
    return [f for run in runs if (f := _run_finding(run, changed)) is not None]


def _run_finding(run: list[CommentLine], changed: frozenset[int]) -> Finding | None:
    length = sum(1 for comment in run if comment.text.lstrip("#").strip())
    if length <= MAX_LINES or not _touched(run[0].line, run[-1].line, changed):
        return None
    return Finding(run[0].line, "comment block", length)


def check(source: str, changed: frozenset[int]) -> Verdict:
    """Blocks over the cap and line references that overlap `changed`, each in source order.

    Source that does not parse yields nothing; `judged_lines` is what stops that from becoming a
    way to land an essay beside a syntax error and never have it looked at again.
    """
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return Verdict([], [])
    docs = _docstrings(tree, changed)
    findings = sorted(_docstring_findings(docs) + _comment_findings(source, changed))
    return Verdict(findings, sorted(_line_refs(docs, source, changed)))


def _parses(source: str) -> bool:
    try:
        ast.parse(source)
    except SyntaxError:
        return False
    return True


def _changed_lines(before: str, after: str) -> frozenset[int]:
    """1-indexed lines of `after` this edit wrote, plus the seam either side of a pure deletion.

    A deletion has an empty range in `after`, so counting only written lines would let an agent
    trim a sixteen-line essay to four for free while a one-character edit is denied.
    """
    diff = difflib.SequenceMatcher(a=before.splitlines(), b=after.splitlines(), autojunk=False)
    edits = ((j1, j2) for tag, _, _, j1, j2 in diff.get_opcodes() if tag != "equal")
    spans = (range(j1 + 1, j2 + 1) if j2 > j1 else range(max(j1, 1), j1 + 2) for j1, j2 in edits)
    return frozenset(line for span in spans for line in span)


def judged_lines(before: str, after: str) -> frozenset[int]:
    """The lines of `after` this call is answerable for — normally the ones it changed.

    A `before` that did not parse was never judged at all, so the edit that makes the file parse
    answers for all of it; otherwise an essay lands beside a syntax error and is never seen again.
    """
    if not _parses(before):
        return frozenset(range(1, len(after.splitlines()) + 1))
    return _changed_lines(before, after)


def replay(edits: list[Replacement], source: str) -> str:
    """`source` with every replacement applied in order, as the edit tool would apply them.

    An `Edit`'s `new_string` is a fragment — an indented method, half a class — that rarely parses
    alone, so the rules need the whole file as this call would leave it, not the fragment.
    """
    for edit in edits:
        source = source.replace(edit["old_string"], edit["new_string"], -1 if edit.get("replace_all") else 1)
    return source


def _emit(reason: str) -> None:
    sys.stdout.write(
        json.dumps(
            {
                "hookSpecificOutput": {
                    "hookEventName": "PreToolUse",
                    "permissionDecision": "deny",
                    "permissionDecisionReason": reason,
                },
            },
        )
        + "\n",
    )


def reason_for(verdict: Verdict, tool_name: str) -> str:
    """The deny text: each offender with what is wrong with it, then what to do about it."""
    parts = []
    if verdict.findings:
        listed = "\n".join(f"  line {f.line} — {f.what}: {f.length} lines" for f in verdict.findings)
        parts.append(_REASON.format(count=len(verdict.findings), tool=tool_name, limit=MAX_LINES, listed=listed))
    if verdict.refs:
        listed = "\n".join(f"  line {r.line} — {r.what}: `{r.ref}`" for r in verdict.refs)
        parts.append(_REF_REASON.format(count=len(verdict.refs), tool=tool_name, listed=listed))
    return "\n\n".join(parts)


def main() -> None:
    """PreToolUse entry point: read the stdin payload, emit a deny, or nothing."""
    data = json.loads(sys.stdin.read())
    tool_name = data["tool_name"]
    if tool_name not in EDIT_TOOLS:
        return
    # `tool_input`'s shape is chosen by `tool_name`, which sits outside the record — a tag no
    # TypedDict union can narrow on, so the dispatch reads each tool's own keys here instead.
    tool_input = data["tool_input"]
    path = Path(tool_input["file_path"])
    if path.suffix != GUARDED_SUFFIX:
        return
    before = path.read_text(encoding="utf-8") if path.is_file() else ""
    # An `Edit` is a `MultiEdit` of one, so both take the same path.
    after = tool_input["content"] if tool_name == "Write" else replay(tool_input.get("edits", [tool_input]), before)
    verdict = check(after, judged_lines(before, after))
    if verdict.findings or verdict.refs:
        _emit(reason_for(verdict, tool_name))


if __name__ == "__main__":
    main()
