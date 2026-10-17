#!/usr/bin/env python3
"""Whether a PR title says what changes, judged by a model — the one check here a regex cannot make.

`Tasks agent: the agent decides a text's shop, or leaves it to both piles` has no identifier in it
and is still unreadable: its words only mean something inside the code. Telling that apart from a
plain claim about the product takes a model; one Messages API call answers in about a second.

A title the judge suggested itself passes without a second call. The verdict is not deterministic,
and an agent that took the suggestion must not be sent round again by a judge that changed its mind.
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from pathlib import Path
from typing import TypedDict, cast

# Sonnet, not Haiku: on 17 real titles Haiku failed 9 for style and rewrote jargon into jargon;
# Sonnet failed the 3 a reader cannot decode, and the same 3 on a second run.
MODEL = "claude-sonnet-5"
SYSTEM = (
    "You gate pull-request titles for a small product team. The only question: can a teammate who "
    "has not read the code tell from the title alone which part of the product changes and how? "
    "Be lenient — a plain title that names the area and the change passes even if you could word "
    "it better; never fail a title for style. Fail it only when the reader must open the code to "
    "decode it: words that only mean something inside the code (internal class, variable or "
    "concept names used as if known, e.g. 'piles', 'a text's shop'), or a title that names no "
    "change at all. Judge only the title; the body is context for the rewrite."
)
# A forced tool call makes the verdict structured: free text came back as "PASS\n\nFAIL: …".
VERDICT_TOOL = {
    "name": "verdict",
    "description": "Record whether the title passes, and a rewrite when it fails.",
    "input_schema": {
        "type": "object",
        "properties": {
            "passes": {"type": "boolean"},
            "rewrite": {
                "type": "string",
                "description": "When it fails: the better title, at most 80 characters, in the "
                "language of the original title (not of the body), using only facts from the input. "
                "Required when it fails; empty when it passes.",
            },
        },
        "required": ["passes", "rewrite"],
    },
}
BODY_CONTEXT = 1500  # the opening of the body says what the PR is about; the rest is detail
SUGGESTIONS = Path.home() / ".cache" / "pr-flow" / "title-suggestions.tsv"


class JudgeUnavailableError(Exception):
    """The judge gave no usable verdict: no key, the API failed, or the answer was malformed."""


class Verdict(TypedDict):
    """The judge's forced `verdict` tool input."""

    passes: bool
    rewrite: str


def _verdict(title: str, body: str) -> Verdict:
    key = os.environ.get("ANTHROPIC_API_KEY")
    if not key:
        raise JudgeUnavailableError("ANTHROPIC_API_KEY is not set")
    request = urllib.request.Request(  # noqa: S310 — the API URL, or the operator's own ANTHROPIC_BASE_URL
        os.environ.get("ANTHROPIC_BASE_URL", "https://api.anthropic.com") + "/v1/messages",
        data=json.dumps(
            {
                "model": MODEL,
                "max_tokens": 200,
                "system": SYSTEM,
                "tools": [VERDICT_TOOL],
                "tool_choice": {"type": "tool", "name": "verdict"},
                "messages": [{"role": "user", "content": f"Title: {title}\n\nBody (opening):\n{body[:BODY_CONTEXT]}"}],
            }
        ).encode(),
        headers={
            "x-api-key": key,
            "anthropic-version": "2023-06-01",
            "content-type": "application/json",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as response:  # noqa: S310
            reply = json.load(response)
    except (urllib.error.URLError, TimeoutError) as error:
        raise JudgeUnavailableError(f"Messages API call failed: {error}") from error
    block = reply["content"][0]
    if block["type"] != "tool_use":
        raise JudgeUnavailableError(f"no verdict in the reply (stop_reason {reply.get('stop_reason')})")
    verdict = cast("Verdict", block["input"])
    if not verdict["passes"] and not verdict["rewrite"].strip():
        raise JudgeUnavailableError("the judge failed the title but gave no rewrite")
    return verdict


def _suggested(repo: Path) -> list[str]:
    lines = SUGGESTIONS.read_text().splitlines() if SUGGESTIONS.is_file() else []
    return [line for line in lines if line.startswith(f"{repo}\t")]


def rewrite(title: str, body: str, repo: Path) -> str | None:
    """The judge's better title when this one fails, None when it passes; raises JudgeUnavailableError."""
    # A suggestion is accepted once, in the repo it was made for, then forgotten.
    entry = f"{repo}\t{title}"
    if entry in _suggested(repo):
        SUGGESTIONS.write_text("".join(f"{line}\n" for line in SUGGESTIONS.read_text().splitlines() if line != entry))
        return None
    verdict = _verdict(title, body)
    if verdict["passes"]:
        return None
    better = verdict["rewrite"].strip()
    SUGGESTIONS.parent.mkdir(parents=True, exist_ok=True)
    with SUGGESTIONS.open("a") as file:
        file.write(f"{repo}\t{better}\n")
    return better
