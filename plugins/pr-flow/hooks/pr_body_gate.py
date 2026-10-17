#!/usr/bin/env python3
"""PreToolUse hook: a PR body a human cannot read in a minute is denied before it is posted.

The `pr-description` skill already says what a readable body is, and agents still post the diff
narrated as a bullet list of identifiers — every line true, the point buried under them. This hook
checks the three things that are mechanical: a comparison table or a mermaid diagram exists, the
bullets are decisions rather than symbols, and the prose carries no more than a handful of inline
code spans per hundred words. Whether the numbers were measured stays with the skill.

The body is read from the `--body-file` the skill mandates. An inline `--body`, a heredoc or stdin
cannot be judged before it runs, so they are denied with the instruction to write a file first;
the same goes for `gh api` writing a PR body, which is the obvious way round this gate.
"""

from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path
from typing import Literal, NamedTuple

from bash_command import blank_quotes, shell_bodies
from title_judge import rewrite

_PR_CALL_RE = re.compile(r"\bgh\s+pr\s+(create|edit)\b")
# Matched on the raw command: a body is quoted (`-f "body=…"`), and blanking would hide it.
_API_WRITE_RE = re.compile(
    r"\bgh\s+api\b[^\n;&|]*(?:\bpulls\b[^\n;&|]*(?:\bbody=|--input)|\bgraphql\b[^\n;&|]*\bmutation\b)"
)
_SEGMENT_END_RE = re.compile(r"&&|\|\||[;|\n]")
# Flags are matched on the blanked arguments so `--title "drop -b flag"` is not a body flag; the
# value is then read from the raw text at the same offset, quotes and spaces intact.
_BODY_FILE_FLAG_RE = re.compile(r"(?<!\S)(?:--body-file|-F)(?=[=\s])")
_FLAG_VALUE_RE = re.compile(r"(?:=|\s+)(\"[^\"]*\"|'[^']*'|\S+)")
_TITLE_FLAG_RE = re.compile(r"(?<!\S)(?:--title|-t)(?=[=\s])")
_INLINE_BODY_RE = re.compile(r"(?<!\S)(?:(?:--body|-b)(?:=|\s)|--fill\b)")

_FENCE_RE = re.compile(r"^```.*?^```", re.MULTILINE | re.DOTALL)
_TABLE_RULE_RE = re.compile(r"^\s*\|?\s*:?-+:?\s*(?:\|\s*:?-+:?\s*)+\|?\s*$", re.MULTILINE)
_HEADING_RE = re.compile(r"^#{1,6}\s+(.*)$", re.MULTILINE)
# Checks list the exact commands that ran, as the skill requires — inline code is right there.
_CHECKS_HEADING_RE = re.compile(r"^\W*(?:checks?|verification|tests?|testing|проверк\w*)\b", re.IGNORECASE)
_BULLET_RE = re.compile(r"^\s*(?:[-*+]|\d+\.)\s+(.*)$", re.MULTILINE)
_CODE_SPAN_RE = re.compile(r"`[^`\n]+`")

MIN_LIST = 4  # a list this long with mostly code in it is a file list, not a set of decisions
MAX_SPANS_PER_100_WORDS = 6
QUOTED_BULLETS = 3
QUOTE_WIDTH = 100

DENY_PREFIX = (
    "PR rejected — a reviewer cannot get the point of it in a minute. Fix what is listed below, "
    "then rerun the same command. Do not route around this with `gh api` or the web UI: the text "
    "is the problem, not the tool.\n\n"
)
NO_VISUAL = (
    "No before/after. Add a markdown table comparing before and after (`| | Before | After |`) "
    "with the measured numbers of the behaviour that changed. A new feature with no 'before' gets a "
    "mermaid diagram of the new flow instead (```mermaid fence)."
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
API_WRITE = (
    "Do not write a PR body through `gh api`: write it with the Write tool, then "
    "`gh pr create --body-file <path>` or `gh pr edit <number> --body-file <path>`."
)
MISSING_FILE = (
    "The body file `{path}` does not exist at `{resolved}`. Write it with the Write tool in its own "
    "step, then rerun this command with an absolute path — a file created earlier in the same Bash "
    "call is not there yet when this check runs, and a `cd` in the command is not followed."
)
BAD_TITLE = (
    "The title `{title}` needs the code to decode — its words only mean something inside the "
    "codebase. Say which part of the product changes and how, in plain words. For example:\n"
    '  --title "{better}"\n'
    "That exact title is accepted as is; check it against the facts before you use it."
)
UNREADABLE = "The body file `{path}` cannot be read as UTF-8 text ({error}). Rewrite it with the Write tool."


class PrCall(NamedTuple):
    """One `gh pr create|edit` invocation found in a Bash command."""

    verb: Literal["create", "edit"]
    args: str  # this call's own argument text, up to the next `&&`, `;`, `|` or newline
    blanked: str  # the same text with quoted spans blanked; offsets match `args`


def _clip(line: str) -> str:
    return line if len(line) <= QUOTE_WIDTH else line[: QUOTE_WIDTH - 1] + "…"


def pr_calls(command: str) -> list[PrCall]:
    """Every `gh pr create|edit` call in a Bash command, including those inside `sh -c` / `eval`."""
    calls = []
    for source in (command, *shell_bodies(command)):
        blanked = blank_quotes(source)
        for found in _PR_CALL_RE.finditer(blanked):
            end = _SEGMENT_END_RE.search(blanked, found.end())
            stop = end.start() if end else len(source)
            verb: Literal["create", "edit"] = "create" if found.group(1) == "create" else "edit"
            calls.append(PrCall(verb, source[found.end() : stop], blanked[found.end() : stop]))
    return calls


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
        worst = sorted(coded, key=lambda b: len(_CODE_SPAN_RE.findall(b)) / len(b.split()), reverse=True)
        quoted = "\n".join(f"  - {_clip(bullet)}" for bullet in worst[:QUOTED_BULLETS])
        found.append(DIFF_NARRATION.format(code=len(coded), total=len(bullets), quoted=quoted))
    spans = len(_CODE_SPAN_RE.findall(prose))
    words = len(prose.split())
    if spans * 100 > MAX_SPANS_PER_100_WORDS * words:
        found.append(CODE_SOUP.format(spans=spans, words=words))
    return found


def flag_value(call: PrCall, flag_re: re.Pattern[str]) -> str | None:
    """The flag's value with quotes stripped, `-` for a flag given no value, None when it is absent."""
    flag = flag_re.search(call.blanked)
    if flag is None:
        return None
    value = _FLAG_VALUE_RE.match(call.args, flag.end())
    return value.group(1).strip("'\"") if value else "-"


def _resolve(cwd: Path, path: str) -> Path:
    return cwd / Path(os.path.expandvars(path)).expanduser()


def body_problems(call: PrCall, cwd: Path) -> list[str]:
    """What is wrong with the body this one `gh pr` call would post; empty when it posts none."""
    path = flag_value(call, _BODY_FILE_FLAG_RE)
    if path is None:
        # A title, label or reviewer edit leaves the body alone; a create always posts one.
        posts_body = call.verb == "create" or _INLINE_BODY_RE.search(call.blanked)
        return [NOT_A_FILE.format(verb=call.verb)] if posts_body else []
    if path == "-":  # stdin, or a flag with no value at all
        return [NOT_A_FILE.format(verb=call.verb)]
    file = _resolve(cwd, path)
    if not file.is_file():
        return [MISSING_FILE.format(path=path, resolved=file)]
    try:
        body = file.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as error:  # a gate that crashes lets the body through
        return [UNREADABLE.format(path=path, error=error)]
    return violations(body)


def title_problems(call: PrCall, cwd: Path) -> list[str]:
    """The model's verdict on this call's `--title`; run only once its body passed, since it costs a call."""
    title = flag_value(call, _TITLE_FLAG_RE)
    if title is None:
        return []
    path = flag_value(call, _BODY_FILE_FLAG_RE)
    body = "" if path is None or path == "-" else _resolve(cwd, path).read_text(encoding="utf-8")
    better = rewrite(title, body)
    return [BAD_TITLE.format(title=title, better=better)] if better else []


def problems(command: str, cwd: Path) -> list[str]:
    """Everything wrong with the PR titles and bodies this Bash command would post."""
    if _API_WRITE_RE.search(command):
        return [API_WRITE]
    found = []
    for call in pr_calls(command):
        found += body_problems(call, cwd) or title_problems(call, cwd)
    return found


def main() -> None:
    """PreToolUse entry point: emit one deny, or nothing at all."""
    data = json.loads(sys.stdin.read())
    found = problems(data["tool_input"]["command"], Path(data["cwd"]))
    if not found:
        return
    reason = DENY_PREFIX + "\n\n".join(f"{n}. {text}" for n, text in enumerate(found, start=1))
    output = {
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "deny",
            "permissionDecisionReason": reason,
        }
    }
    sys.stdout.write(json.dumps(output) + "\n")


if __name__ == "__main__":
    main()
