#!/usr/bin/env python3
"""Docstring-length gate for Claude Code file edits — three lines, then stop.

A model asked to write a contract writes it in the docstrings, and an essay above a function reads
as finished work long enough to survive review. No rule about *what* a comment may say can be
enforced without judging meaning, which a hook cannot do. A line count can.

Only `deny` is ever emitted, and only for lines this very edit touches.
"""

from __future__ import annotations

import ast
import difflib
import io
import json
import sys
import tokenize
from pathlib import Path
from typing import NamedTuple, NotRequired, TypedDict

MAX_LINES = 3

GUARDED_SUFFIX = ".py"


_REASON = (
    "{count} docstring(s)/comment(s) in this {tool} run past {limit} lines:\n\n{listed}\n\n"
    "A docstring describes the thing it sits on, and nothing else: how to call it and what you "
    "get back. The test, from the Google Python Style Guide — it should give enough information "
    "to write a call without reading the function's code. PEP 257 names the contents: behaviour, "
    "arguments, return value, side effects, exceptions raised, restrictions.\n\n"
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


class EditSpec(TypedDict):
    """One replacement, as `Edit` carries it and as each entry of `MultiEdit`'s list does."""

    old_string: str
    new_string: str
    replace_all: NotRequired[bool]


class ToolInput(TypedDict):
    """`tool_input` of a Write/Edit/MultiEdit call; which keys arrive depends on the tool."""

    file_path: str
    content: NotRequired[str]
    old_string: NotRequired[str]
    new_string: NotRequired[str]
    replace_all: NotRequired[bool]
    edits: NotRequired[list[EditSpec]]


class Finding(NamedTuple):
    """One docstring or comment block that runs past the cap."""

    line: int
    what: str
    length: int


class Block(NamedTuple):
    """A run of prose and the source lines it spans, both ends inclusive."""

    start: int
    end: int
    text: str


def _touched(block: Block, changed: frozenset[int]) -> bool:
    """True when this edit wrote any line of the block.

    The whole span, not just its first line: an essay usually grows by appending to a docstring
    whose opening quote was written long ago and has not moved.
    """
    return any(line in changed for line in range(block.start, block.end + 1))


def _docstring(node: ast.AST) -> Block | None:
    body = getattr(node, "body", None)
    first = body[0] if body else None
    if isinstance(first, ast.Expr) and isinstance(first.value, ast.Constant) and isinstance(first.value.value, str):
        return Block(first.value.lineno, first.value.end_lineno or first.value.lineno, first.value.value)
    return None


def _docstring_findings(tree: ast.AST, changed: frozenset[int]) -> list[Finding]:
    """Function and class docstrings over the cap. Module docstrings are deliberately absent.

    One orientation paragraph per file is the cheapest context a reader gets, and unlike a method's
    it sits on no signature that could have carried the information instead.
    """
    out = []
    for node in ast.walk(tree):
        if not isinstance(node, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        block = _docstring(node)
        if block is None or not _touched(block, changed):
            continue
        length = sum(1 for line in block.text.splitlines() if line.strip())
        if length > MAX_LINES:
            out.append(Finding(block.start, f"docstring of `{node.name}`", length))
    return out


def _own_line_comments(source: str) -> list[int]:
    """Lines holding nothing but a `#` comment. A trailing one annotates its own line, not a block."""
    try:
        tokens = list(tokenize.generate_tokens(io.StringIO(source).readline))
    except (tokenize.TokenError, IndentationError, SyntaxError):
        return []
    return [t.start[0] for t in tokens if t.type == tokenize.COMMENT and t.line.lstrip().startswith("#")]


def _comment_findings(source: str, changed: frozenset[int]) -> list[Finding]:
    """Runs of consecutive own-line comments, capped the same as a docstring.

    Without this the cap is one keystroke away from being routed around: the essay moves from
    inside the quotes to a `#` block directly above them and says exactly the same thing.
    """
    runs: list[Block] = []
    for line in _own_line_comments(source):
        if runs and line == runs[-1].end + 1:
            runs[-1] = runs[-1]._replace(end=line)
        else:
            runs.append(Block(line, line, ""))
    over = (r for r in runs if r.end - r.start + 1 > MAX_LINES and _touched(r, changed))
    return [Finding(r.start, "comment block", r.end - r.start + 1) for r in over]


def check(source: str, changed: frozenset[int]) -> list[Finding]:
    """Every docstring and comment block over the cap that starts on a changed line.

    Unparseable source yields nothing — an edit landing a file mid-rewrite is the editor's
    business, and the next edit over the finished file re-runs the check anyway.
    """
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return []
    return sorted(_docstring_findings(tree, changed) + _comment_findings(source, changed))


def changed_lines(before: str, after: str) -> frozenset[int]:
    """1-indexed lines of `after` that `before` does not already hold in that position.

    Scoping to these keeps the hook off code the agent did not touch: changing one function in an
    old file must not be denied over an essay written years ago.
    """
    diff = difflib.SequenceMatcher(a=before.splitlines(), b=after.splitlines(), autojunk=False)
    spans = (range(j1 + 1, j2 + 1) for tag, _, _, j1, j2 in diff.get_opcodes() if tag != "equal")
    return frozenset(line for span in spans for line in span)


def _apply(source: str, old: str, new: str, *, every: bool) -> str:
    return source.replace(old, new) if every else source.replace(old, new, 1)


def post_edit_source(tool_name: str, tool_input: ToolInput, before: str) -> str:
    """The file's content as this call would leave it.

    An `Edit`'s `new_string` is a fragment — an indented method, half a class — that rarely parses
    alone. Replaying the edit over the file on disk gives text that does.
    """
    if tool_name == "Write":
        return tool_input["content"]
    if tool_name == "MultiEdit":
        for edit in tool_input["edits"]:
            before = _apply(before, edit["old_string"], edit["new_string"], every=bool(edit.get("replace_all")))
        return before
    return _apply(
        before,
        tool_input["old_string"],
        tool_input["new_string"],
        every=bool(tool_input.get("replace_all")),
    )


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


def reason_for(findings: list[Finding], tool_name: str) -> str:
    """The deny text: every offending block with its length, then what to do about it."""
    listed = "\n".join(f"  line {f.line} — {f.what}: {f.length} lines" for f in findings)
    return _REASON.format(count=len(findings), tool=tool_name, limit=MAX_LINES, listed=listed)


def main() -> None:
    """PreToolUse entry point: read the stdin payload, emit a deny, or nothing."""
    data = json.loads(sys.stdin.read())
    tool_input = data["tool_input"]
    path = Path(tool_input["file_path"])
    if path.suffix != GUARDED_SUFFIX:
        return
    before = path.read_text(encoding="utf-8") if path.is_file() else ""
    after = post_edit_source(data["tool_name"], tool_input, before)
    findings = check(after, changed_lines(before, after))
    if findings:
        _emit(reason_for(findings, data["tool_name"]))


if __name__ == "__main__":
    main()
