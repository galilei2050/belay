#!/usr/bin/env python3
"""Keep every CLAUDE.md on one skeleton: a dated preamble and two decision sections.

Two entry points share the checks. As a PostToolUse hook (JSON on stdin) it runs after a
Write/Edit of a CLAUDE.md: it stamps today's date into the `Updated:` line, lints the result, and
hands any findings back as a `block` so the agent fixes them while the file is still open. As a
CLI (`claude_md_hook.py <file-or-dir>...`) it audits existing files and adds the one check that
only makes sense at rest — how far the code beside a CLAUDE.md has moved since the file last did.

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

FILE_NAME = "CLAUDE.md"
# Anthropic's memory docs: past ~200 lines a CLAUDE.md costs context and adherence drops.
MAX_LINES = 200
# Commits to the directory since its CLAUDE.md last changed before the audit calls the file stale.
STALE_COMMITS = 20
REQUIRED_SECTIONS = ("Business decisions", "Technical decisions")

GIT = shutil.which("git")

_UPDATED_RE = re.compile(r"^Updated: \d{4}-\d{2}-\d{2}$")
_FENCE_RE = re.compile(r"^\s*(```|~~~)")
_CODE_SPAN_RE = re.compile(r"`([^`\s]+)`")
# Placeholders, globs, env vars, URLs, `file:line` refs and flags are not paths to look up.
_NOT_A_PATH_RE = re.compile(r"[<>*{}$|=:@#,()\[\]]|^-")


def stamp(text: str, today: str) -> str:
    """`text` with its `Updated:` line set to `today`, inserted under the H1 when absent."""
    line = f"Updated: {today}"
    lines = text.splitlines()
    for index, existing in enumerate(lines):
        if _UPDATED_RE.match(existing):
            lines[index] = line
            break
    else:
        at = 1 if lines and lines[0].startswith("# ") else 0
        lines[at:at] = ["", line] if at else [line, ""]
    return "\n".join(lines) + "\n"


def prose_lines(text: str) -> list[str]:
    """The lines outside fenced code blocks — a `## ` or a path inside a fence is an example."""
    kept: list[str] = []
    fenced = False
    for line in text.splitlines():
        if _FENCE_RE.match(line):
            fenced = not fenced
        elif not fenced:
            kept.append(line)
    return kept


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


def decisions(text: str) -> set[str]:
    """Normalised bullets of the decision sections; continuation lines fold into their bullet."""
    bullets: list[str] = []
    for title in REQUIRED_SECTIONS:
        for line in sections(text).get(title.lower(), []):
            if line.startswith("- "):
                bullets.append(line[2:])
            elif bullets and line.startswith(" ") and line.strip():
                bullets[-1] += " " + line
    normalised = {" ".join(bullet.lower().split()) for bullet in bullets}
    return {bullet for bullet in normalised if not bullet.startswith("none")}


def repo_root(start: Path) -> Path | None:
    """Nearest ancestor of `start` holding a `.git`, or None outside a checkout."""
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
        parts = span.strip("/").split("/")
        if len(parts) < 2 or _NOT_A_PATH_RE.search(span):  # noqa: PLR2004 — a path needs two segments
            continue
        candidates = [Path(span).expanduser()] if span.startswith(("~", "/")) else []
        candidates += [base / span.strip("/") for base in bases]
        anchored = any((c.parents[len(parts) - 2]).is_dir() for c in candidates)
        if anchored and not any(c.exists() for c in candidates) and span not in missing:
            missing.append(span)
    return missing


def lint(path: Path, text: str) -> list[str]:
    """Every skeleton finding for the CLAUDE.md at `path` holding `text`."""
    findings: list[str] = []
    lines = text.splitlines()
    if not any(_UPDATED_RE.match(line) for line in lines):
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
            findings.append(f"`## {title}` is empty — record the decisions, or write `None at this level.`")
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
    # S603: resolved binary, fixed argv built here, no shell.
    return subprocess.run((GIT, *args), cwd=cwd, check=True, capture_output=True, text=True).stdout.strip()  # noqa: S603


def staleness(path: Path) -> list[str]:
    """A finding when the directory has moved `STALE_COMMITS`+ commits past its CLAUDE.md."""
    last = _git(path.parent, "log", "-1", "--format=%H", "--", path.name)
    if not last:
        return []  # never committed — nothing to be older than
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
        reason = f"{path} breaks the CLAUDE.md skeleton (see the `claude-md` skill):\n{listing}\nFix these now."
        sys.stdout.write(json.dumps({"decision": "block", "reason": reason}) + "\n")


if __name__ == "__main__":
    if sys.argv[1:]:
        sys.exit(audit(sys.argv[1:]))
    main()
