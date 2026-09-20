#!/usr/bin/env python3
"""Keep every CLAUDE.md on one skeleton: a dated preamble and two decision sections.

Two entry points share the checks. As a PostToolUse hook (JSON on stdin) it runs after a
Write/Edit of a CLAUDE.md: it stamps today's date into the `Updated:` line, lints the result, and
hands any findings back as a `block` so the agent fixes them while the file is still open. As a
CLI (`claude_md.py <file-or-dir>...`) it audits existing files and adds what only makes
sense at rest — how far the code beside a CLAUDE.md has moved since the file last did.

The date is stamped here, never typed: a hand-kept date stops moving on the second edit, and the
model reading the file has no `git log` to tell it the file is old.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from typing import NamedTuple

FILE_NAME = "CLAUDE.md"
# Anthropic's memory docs: past ~200 lines a CLAUDE.md costs context and adherence drops.
MAX_LINES = 200
# Commits to the directory since its CLAUDE.md last changed before the audit calls the file stale.
STALE_COMMITS = 20
REQUIRED_SECTIONS = ("Business decisions", "Technical decisions")
# The whole body of a required section at a level that decided nothing.
NO_DECISIONS = "None at this level."
# NotebookEdit names its target `notebook_path`; listing the tools keeps it out whatever the matcher lets through.
EDIT_TOOLS = frozenset({"Write", "Edit", "MultiEdit"})

GIT = shutil.which("git")

_UPDATED_RE = re.compile(r"^Updated: \d{4}-\d{2}-\d{2}$")
_FENCE_RE = re.compile(r"^\s*(```|~~~)")
_CODE_SPAN_RE = re.compile(r"`([^`\s]+)`")
# Placeholders, globs, env vars, URLs, `file:line` refs and flags are not paths to look up.
_NOT_A_PATH_RE = re.compile(r"[<>*{}$|=:@#,()\[\]]|^-")


def prose_indices(lines: list[str]) -> list[int]:
    """Indices of the lines outside fenced code blocks — a heading, a date or a path inside a fence is an example."""
    kept: list[int] = []
    fenced = False
    for index, line in enumerate(lines):
        if _FENCE_RE.match(line):
            fenced = not fenced
        elif not fenced:
            kept.append(index)
    return kept


def prose_lines(text: str) -> list[str]:
    """The lines of `text` outside fenced code blocks."""
    lines = text.splitlines()
    return [lines[index] for index in prose_indices(lines)]


def stamp(text: str, today: str) -> str:
    """`text` with its `Updated:` line set to `today`; inserted under the H1 (or on top, with no H1) when absent."""
    line = f"Updated: {today}"
    lines = text.splitlines()
    prose = prose_indices(lines)
    existing = next((i for i in prose if _UPDATED_RE.match(lines[i])), None)
    if existing is not None:
        lines[existing] = line
        return "\n".join(lines) + "\n"
    title = next((i for i in prose if lines[i].startswith("# ")), None)
    at = 0 if title is None else title + 1
    # Blank lines on both sides, or markdown folds the stamp into the neighbouring paragraph.
    before = [] if title is None else [""]
    after = [""] if at < len(lines) and lines[at].strip() else []
    lines[at:at] = [*before, line, *after]
    return "\n".join(lines) + "\n"


def sections(text: str) -> dict[str, list[str]]:
    """Body lines of every `## ` section, keyed by lower-cased title."""
    found: dict[str, list[str]] = {}
    body: list[str] | None = None
    for line in prose_lines(text):
        if line.startswith(("# ", "## ")):
            body = found.setdefault(line[3:].strip().lower(), []) if line.startswith("## ") else None
        elif body is not None:
            body.append(line)
    return found


def bullets(body: list[str]) -> list[str]:
    """The `- ` bullets of one section body; indented continuation lines fold into their bullet."""
    found: list[str] = []
    for line in body:
        if line.startswith("- "):
            found.append(line[2:])
        elif found and line.startswith(" ") and line.strip():
            found[-1] += " " + line
    return found


def decisions(text: str) -> set[str]:
    """Normalised bullets of the decision sections, minus the `NO_DECISIONS` placeholder.

    The placeholder is dropped by exact match: every childless level carries it, so it would
    otherwise be reported as a duplicate of its parent's.
    """
    found = sections(text)
    raw = [bullet for title in REQUIRED_SECTIONS for bullet in bullets(found.get(title.lower(), []))]
    normalised = {" ".join(bullet.lower().split()) for bullet in raw}
    return normalised - {NO_DECISIONS.lower()}


def repo_root(start: Path) -> Path | None:
    """`start` or its nearest ancestor holding a `.git`, or None outside a checkout."""
    return next((p for p in (start, *start.parents) if (p / ".git").exists()), None)


def missing_paths(path: Path, text: str) -> list[str]:
    """Code-span paths whose first segment exists but whose whole does not.

    Precision over recall: `origin/main` or `owner/repo` never resolve a first segment, so they
    are left alone, while `plugins/renamed-hook/x.py` — a real tree, a gone leaf — is caught.
    """
    root = repo_root(path.parent)
    bases = [path.parent, *([root] if root else [])]
    missing: list[str] = []
    for span in _CODE_SPAN_RE.findall("\n".join(prose_lines(text))):
        lookups = [] if _NOT_A_PATH_RE.search(span) else lookups_for(span, bases)
        anchored = any(lookup.anchor.is_dir() for lookup in lookups)
        if anchored and not any(lookup.target.exists() for lookup in lookups) and span not in missing:
            missing.append(span)
    return missing


class Lookup(NamedTuple):
    """One place a code-span path may live: its first segment there, and the whole path there."""

    anchor: Path
    target: Path


def lookups_for(span: str, bases: list[Path]) -> list[Lookup]:
    """Where `span` could resolve: under each base, plus `~/…` in the home dir and `/…` on disk.

    Segments come from `Path.parts`, so `./a/b` and `a//b` anchor on `a` like `a/b` does. Only a
    literal `~/` is expanded — `~user/…` stays relative, and `expanduser()` raises on an unknown user.
    """
    relative = Path(span.lstrip("/"))
    if len(relative.parts) < 2:  # noqa: PLR2004 — a single segment is a word, not a path
        return []
    lookups = [Lookup(base / relative.parts[0], base / relative) for base in bases]
    if span.startswith("~/"):
        lookups.append(Lookup(Path.home(), Path(span).expanduser()))
    elif span.startswith("/"):
        lookups.append(Lookup(Path("/", relative.parts[0]), Path(span)))
    return lookups


def lint(path: Path, text: str) -> list[str]:
    """Every skeleton finding for the CLAUDE.md at `path` holding `text`."""
    findings: list[str] = []
    lines = text.splitlines()
    if not any(_UPDATED_RE.match(line) for line in prose_lines(text)):
        findings.append("no `Updated: YYYY-MM-DD` line under the title")
    if len(lines) > MAX_LINES:
        findings.append(
            f"{len(lines)} lines, limit is {MAX_LINES} — move directory-specific rules to a nested CLAUDE.md or "
            "`.claude/rules` with `paths:`, procedures to a skill, must-always actions to a hook"
        )
    found = sections(text)
    for title in REQUIRED_SECTIONS:
        body = found.get(title.lower())
        if body is None:
            findings.append(f"missing section `## {title}`")
        elif not any(line.strip() for line in body):
            findings.append(f"`## {title}` is empty — record the decisions, or write `{NO_DECISIONS}`")
    findings += [f"referenced path does not exist: `{span}`" for span in missing_paths(path, text)]
    return findings + duplicate_decisions(path, text)


def duplicate_decisions(path: Path, text: str) -> list[str]:
    """Decisions this file repeats from a CLAUDE.md above it — a decision lives at its top level."""
    own = decisions(text)
    findings: list[str] = []
    for ancestor in path.parent.parents:
        parent_file = ancestor / FILE_NAME
        if parent_file.is_file():
            repeated = own & decisions(parent_file.read_text(encoding="utf-8"))
            findings += [f"decision already recorded in {parent_file}: `{bullet}`" for bullet in sorted(repeated)]
    return findings


def _git(cwd: Path, *args: str) -> str:
    if GIT is None:
        raise RuntimeError("git is required to audit CLAUDE.md files")
    # stderr is left on the terminal: with check=True, git's own "fatal: …" is the only explanation there is.
    # S603: resolved binary, literal git subcommands, values passed as argv after `--`, no shell.
    return subprocess.run((GIT, *args), cwd=cwd, check=True, stdout=subprocess.PIPE, text=True).stdout.strip()  # noqa: S603


def staleness(path: Path) -> list[str]:
    """A finding when the directory has moved `STALE_COMMITS`+ commits past its CLAUDE.md."""
    last = _git(path.parent, "log", "-1", "--format=%H", "--", path.name)
    if not last:
        return ["never committed — staleness unknown"]  # silence would read as "fresh"
    moved = int(_git(path.parent, "rev-list", "--count", f"{last}..HEAD", "--", ".", f":(exclude){path.name}"))
    if moved < STALE_COMMITS:
        return []
    return [f"stale: {moved} commits touched this directory since the file last changed"]


def audit_targets(args: list[str]) -> list[Path]:
    """CLAUDE.md files named by `args`; a directory means its git-tracked ones."""
    targets: list[Path] = []
    for arg in args:
        given = Path(arg).resolve()
        if given.is_dir():
            tracked = _git(given, "ls-files", "--", FILE_NAME, f"**/{FILE_NAME}")
            targets += [given / name for name in tracked.splitlines()]
        else:
            targets.append(given)
    return targets


def audit(args: list[str]) -> int:
    """CLI entry point: print findings per file; exit status 1 when there are any."""
    failed = False
    for path in audit_targets(args):
        findings = lint(path, path.read_text(encoding="utf-8")) + staleness(path)
        failed = failed or bool(findings)
        for finding in findings:
            sys.stdout.write(f"{path}: {finding}\n")
    return int(failed)


def main() -> None:
    """PostToolUse entry point: stamp the date, then block with the findings, or stay silent."""
    data = json.loads(sys.stdin.read())
    if data["tool_name"] not in EDIT_TOOLS:
        return
    path = Path(data["tool_input"]["file_path"])
    if path.name != FILE_NAME:
        return
    text = path.read_text(encoding="utf-8")
    stamped = stamp(text, datetime.now().astimezone().date().isoformat())
    if stamped != text:
        path.write_text(stamped, encoding="utf-8")
    findings = lint(path, stamped)
    if findings:
        listing = "\n".join(f"- {finding}" for finding in findings)
        skill = "`agent-knowledge:claude-md` skill"
        reason = f"{path} breaks the CLAUDE.md skeleton (see the {skill}):\n{listing}\nFix these now."
        sys.stdout.write(json.dumps({"decision": "block", "reason": reason}) + "\n")


if __name__ == "__main__":
    if sys.argv[1:]:
        sys.exit(audit(sys.argv[1:]))
    main()
