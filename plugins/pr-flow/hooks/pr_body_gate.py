#!/usr/bin/env python3
"""PreToolUse hook: a PR body a human cannot read in a minute is denied before it is posted.

The `pr-description` skill already says what a readable body is, and agents still post the diff
narrated as a bullet list of identifiers — every line true, the point buried under them. This hook
checks the three things that are mechanical: a before/after table or a mermaid diagram exists, the
bullets are decisions rather than symbols, and the prose is not mostly inline code. Whether the
numbers were measured or the checks really ran stays with the skill; a regex cannot know.

The body is read from the `--body-file` the skill mandates. An inline `--body`, a heredoc or stdin
cannot be judged before it runs, so they are denied with the instruction to write a file first;
the same goes for `gh api` writing a PR body, which is the obvious way round this gate.
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path
from typing import NamedTuple

# Quoted spans are blanked to the same length, so a match position in the blanked text indexes the
# raw command too: a commit message that merely mentions `gh pr create` is prose, not a call.
_QUOTED_RE = re.compile(r"'[^']*'|\"[^\"]*\"")
_PR_CALL_RE = re.compile(r"\bgh\s+pr\s+(create|edit)\b")
_API_BODY_RE = re.compile(r"\bgh\s+api\b[^\n;&|]*\bpulls\b[^\n;&|]*\bbody=")
_SEGMENT_END_RE = re.compile(r"&&|\|\||[;|\n]")
_BODY_FILE_RE = re.compile(r"(?:--body-file|-F)(?:=|\s+)(\"[^\"]*\"|'[^']*'|\S+)")
_INLINE_BODY_RE = re.compile(r"(?:--body|-b)(?:=|\s)|--fill\b")

_FENCE_RE = re.compile(r"^```.*?^```", re.MULTILINE | re.DOTALL)
_TABLE_RULE_RE = re.compile(r"^\s*\|(?:\s*:?-{3,}:?\s*\|)+\s*$", re.MULTILINE)
_HEADING_RE = re.compile(r"^#{1,6}\s+(.*)$", re.MULTILINE)
# Checks list the exact commands that ran, as the skill requires — inline code is right there.
_CHECKS_HEADING_RE = re.compile(r"check|verif|test|провер", re.IGNORECASE)
_BULLET_RE = re.compile(r"^\s*(?:[-*+]|\d+\.)\s+(.*)$", re.MULTILINE)
_CODE_SPAN_RE = re.compile(r"`[^`\n]+`")

MIN_LIST = 4  # a list this long with mostly code in it is a file list, not a set of decisions
MAX_SPANS_PER_100_WORDS = 6
QUOTED_BULLETS = 3
QUOTE_WIDTH = 100

DENY_PREFIX = (
    "PR body rejected — a reviewer cannot get the point of it in a minute. Rewrite the body file, "
    "then rerun the same command. Do not route around this with `gh api` or the web UI: the body "
    "is the problem, not the tool.\n\n"
)
NO_VISUAL = (
    "No before/after. Add a markdown table `| | Before | After |` with the measured numbers of the "
    "behaviour that changed. A new feature with no 'before' gets a mermaid diagram of the new flow "
    "instead (```mermaid fence)."
)
DIFF_NARRATION = (
    "The bullet list narrates the diff ({code} of {total} bullets are identifiers), e.g.:\n{quoted}\n"
    "The diff already shows these. Replace the list with 2-4 bullets, each one a decision and why, "
    "in plain words — names of files and symbols belong in the diff, not here."
)
CODE_SOUP = (
    "The prose is dense with inline code ({spans} code spans in {words} words, limit "
    f"{MAX_SPANS_PER_100_WORDS} per 100). Say what changed in the system's terms — what a customer, "
    "an operator or a caller sees — and keep at most one or two identifiers that a reviewer must "
    "find in the diff."
)
NOT_A_FILE = (
    "Pass the body as a file: write it with the Write tool, then `gh pr {verb} --body-file <path>`. "
    "An inline --body, a heredoc, stdin or --fill cannot be checked before it is posted, and the "
    "``` fences in a body break inside a shell string anyway."
)
MISSING_FILE = (
    "The body file `{path}` does not exist yet. Write it with the Write tool in its own step, then "
    "rerun this command — a file created earlier in the same Bash call is not there when this "
    "check runs."
)


def _clip(line: str) -> str:
    return line if len(line) <= QUOTE_WIDTH else line[: QUOTE_WIDTH - 1] + "…"


def _blank_quotes(command: str) -> str:
    return _QUOTED_RE.sub(lambda found: " " * len(found.group()), command)


class PrCall(NamedTuple):
    """One `gh pr create|edit` invocation found in a Bash command."""

    verb: str  # create | edit
    args: str  # this call's own argument text, up to the next `&&`, `;`, `|` or newline


def pr_call(command: str) -> PrCall | None:
    """The `gh pr create|edit` call in a Bash command, or None if it makes neither."""
    blanked = _blank_quotes(command)
    found = _PR_CALL_RE.search(blanked)
    if found is None:
        return None
    end = _SEGMENT_END_RE.search(blanked, found.end())
    return PrCall(found.group(1), command[found.end() : end.start() if end else len(command)])


def narrative(body: str) -> str:
    """The body minus code fences and minus the Checks section, where commands are the content."""
    text = _FENCE_RE.sub("", body)
    headings = list(_HEADING_RE.finditer(text))
    kept = [text[: headings[0].start()] if headings else text]
    for index, heading in enumerate(headings):
        end = headings[index + 1].start() if index + 1 < len(headings) else len(text)
        if not _CHECKS_HEADING_RE.search(heading.group(1)):
            kept.append(text[heading.start() : end])
    return "".join(kept)


def violations(body: str) -> list[str]:
    """Every readability rule the body breaks, each worded as the rewrite that fixes it."""
    found = []
    if not _TABLE_RULE_RE.search(body) and "```mermaid" not in body:
        found.append(NO_VISUAL)
    prose = narrative(body)
    bullets = _BULLET_RE.findall(prose)
    coded = [bullet for bullet in bullets if _CODE_SPAN_RE.search(bullet)]
    if len(bullets) >= MIN_LIST and len(coded) * 2 > len(bullets):
        # The most code-dense bullets are the ones that make the case; long ones are cut to a line.
        worst = sorted(coded, key=lambda bullet: len(_CODE_SPAN_RE.findall(bullet)) / len(bullet.split()))
        quoted = "\n".join(f"  - {_clip(bullet)}" for bullet in reversed(worst[-QUOTED_BULLETS:]))
        found.append(DIFF_NARRATION.format(code=len(coded), total=len(bullets), quoted=quoted))
    spans = len(_CODE_SPAN_RE.findall(prose))
    words = len(prose.split())
    if spans * 100 > MAX_SPANS_PER_100_WORDS * words:
        found.append(CODE_SOUP.format(spans=spans, words=words))
    return found


def problems(command: str, cwd: Path) -> list[str]:
    """Everything wrong with the PR body this Bash command would post; empty when it posts none."""
    if _API_BODY_RE.search(_blank_quotes(command)):
        return [NOT_A_FILE.format(verb="create")]
    call = pr_call(command)
    if call is None:
        return []
    verb, args = call
    body_file = _BODY_FILE_RE.search(args)
    if body_file is None:
        # A title, label or reviewer edit leaves the body alone; a create always posts one.
        posts_body = verb == "create" or _INLINE_BODY_RE.search(args)
        return [NOT_A_FILE.format(verb=verb)] if posts_body else []
    path = body_file.group(1).strip("'\"")
    if path == "-":
        return [NOT_A_FILE.format(verb=verb)]
    file = cwd / Path(path).expanduser()
    return violations(file.read_text()) if file.is_file() else [MISSING_FILE.format(path=path)]


def reason(command: str, cwd: Path) -> str | None:
    """The deny reason for this Bash command, or None when it is fine to run."""
    found = problems(command, cwd)
    if not found:
        return None
    return DENY_PREFIX + "\n\n".join(f"{n}. {text}" for n, text in enumerate(found, start=1))


def main() -> None:
    """PreToolUse entry point: emit one deny, or nothing at all."""
    data = json.loads(sys.stdin.read())
    tool_input = data.get("tool_input")
    if data.get("hook_event_name") != "PreToolUse" or data.get("tool_name") != "Bash":
        return
    if not isinstance(tool_input, dict):
        return
    denied = reason(str(tool_input.get("command", "")), Path(data["cwd"]))
    if denied is None:
        return
    output = {
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "deny",
            "permissionDecisionReason": denied,
        }
    }
    sys.stdout.write(json.dumps(output) + "\n")


if __name__ == "__main__":
    main()
