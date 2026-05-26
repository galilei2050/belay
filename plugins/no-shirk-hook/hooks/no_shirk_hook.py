#!/usr/bin/env python3
"""Stop hook: block the agent from ending a turn with an ask-instead-of-do question.

Reads the conversation transcript, looks at the *last* assistant text message,
and if its closing paragraph matches a shirking pattern (and no false-positive
guard fires) returns {"decision":"block","reason":"..."} so Claude Code
re-enters the agent and forces it to do the next obvious step.

One job. No project knowledge, no network, no LLM.
"""

from __future__ import annotations

import gzip
import json
import logging
import re
import sys
from logging.handlers import RotatingFileHandler
from pathlib import Path

HOME = Path.home()

_TAIL_MAX_CHARS = 600
_USER_Q_MAX_LEN = 200
_SNIPPET_MAX_LEN = 160
_SHORT_TAIL_WORDS = 5
_MIN_PARAGRAPHS_FOR_GRAB_PREV = 2

# ── shirking patterns (RU + EN, case-insensitive, matched on the tail) ───────

SHIRK_PATTERNS: dict[str, list[str]] = {
    "ask_to_run": [
        r"\bзапустить\s*\??\s*$",
        r"\bпрогнать\s+(тесты|линт|typecheck)\s*\??\s*$",
        r"\bshould\s+i\s+run\b",
        r"\bwant\s+me\s+to\s+run\b",
        r"\brun\s+(the\s+)?tests\s*\??\s*$",
    ],
    "want_me_to": [
        r"\bхотите,?\s+я\s+(с?делаю|запущу|продолжу|допишу|починю)",
        r"\bхочешь,?\s+(с?делаю|запущу|продолжу|допишу|починю)",
        r"\bесли\s+хочешь,?\s+(запущу|сделаю|продолжу)",
        r"\bwant\s+me\s+to\b",
        r"\bwould\s+you\s+like\s+me\s+to\b",
        r"\bdo\s+you\s+want\s+me\s+to\b",
    ],
    "if_you_want": [
        r"\bесли\s+хотите,?\s+(могу|я)\b",
        r"\bесли\s+нужно,?\s+(могу|я)\b",
        r"\bif\s+you('?d)?\s+like,?\s+i\s+can\b",
        r"\bi\s+can\s+.{1,40}\s+if\s+you('?d)?\s+like\b",
    ],
    "ready_to": [
        r"\bготов(а)?\s+(выполнять|приступить|сделать|продолжить)",
        r"\bready\s+to\s+(proceed|implement|start|continue)\b",
    ],
    "tell_me_if": [
        r"\bскажи(те)?\s+если\s+(нужно|надо|сделать|запустить)",
        r"\bдай(те)?\s+знать,?\s+если\b",
        r"\blet\s+me\s+know\s+if\s+(you('?d)?\s+like|i\s+should)\b",
    ],
    "when_youre_ready": [
        r"\bкогда\s+буде(те|шь)\s+готов\w*",
        r"\bwhen\s+you('?re)?\s+ready\b",
    ],
    "proceed_q": [
        r"\bпродолж(ить|ать)\s*\??\s*$",
        r"\bсоздаю\s*\??\s*$",
        r"\bзапускаю\s*\??\s*$",
        r"\bделаю\s*\??\s*$",
        r"\bshall\s+i\s+(proceed|continue)\b",
        r"\bproceed\s*\?\s*$",
    ],
    "out_of_scope": [
        r"\bвне\s+(рамок|скоупа)\b",
        r"\bout\s+of\s+scope\b",
    ],
    "preexisting": [
        r"\b(ранее|уже)\s+существовавш\w+",
        r"\bpre[-\s]?exist(ing|ed|s)?\b",
    ],
    "partial_done": [
        r"\bоснов\w+\s+функц\w+\s+реализован",
        r"\bэтого\s+достаточно\s+чтобы\s+начать",
        r"\bthat\s+implements\s+the\s+core\b",
        r"\bthis\s+should\s+get\s+you\s+started\b",
    ],
}

# ── false-positive guards: when asking the human IS legitimate ───────────────

DESTRUCTIVE_KEYWORDS = [
    r"force[-\s]?push",
    r"--force\b",
    r"--force-with-lease\b",
    r"\brm\s+-rf\b",
    r"\bdrop\s+table\b",
    r"\btruncate\b",
    r"\bdelete\s+from\b",
    r"\bmigration\b",
    r"\bschema\s+change\b",
    r"\bprod\b",
    r"\bproduction\b",
    r"\bdeploy\b",
    r"\brelease\b",
    r"\bудал(и(ть|ение)|яю|им)\b",
    r"\bснести\b",
    r"\bснесу\b",
    r"\bпрод(а|е|у)?\b",
    r"\bмиграц\w+",
    r"\bзадеплои(ть|м|шь)\b",
    r"\bв\s+проде\b",
    r"\bна\s+проде\b",
]

BUSINESS_AMBIGUITY_MARKERS = [
    r"\bwhich\s+(option|approach|variant)\b",
    r"\bкакой\s+(вариант|подход)\b",
    r"\bкакую\s+(логику|стратегию)\b",
    r"\bprefer(ence)?\b",
    r"\btrade[-\s]?off\b",
    r"\bкомпромисс\b",
    r"\b[A-Z]\w+\s+или\s+[A-Z]\w+\?",
    r"\b[A-Z]\w+\s+or\s+[A-Z]\w+\?",
]


def _gz_namer(name: str) -> str:
    return name + ".gz"


def _gz_rotator(source: str, dest: str) -> None:
    src = Path(source)
    with src.open("rb") as f_in, gzip.open(dest, "wb") as f_out:
        f_out.write(f_in.read())
    src.unlink()


def setup_logging() -> logging.Logger:
    log_dir = HOME / ".claude" / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    logger = logging.getLogger("no_shirk_hook")
    if logger.handlers:
        return logger
    logger.setLevel(logging.INFO)
    handler = RotatingFileHandler(
        log_dir / "no-shirk-hook.log",
        maxBytes=5_000_000,
        backupCount=5,
    )
    handler.namer = _gz_namer
    handler.rotator = _gz_rotator
    handler.setFormatter(logging.Formatter("[%(asctime)s] %(message)s", datefmt="%Y-%m-%d %H:%M:%S"))
    logger.addHandler(handler)
    return logger


# ── text extraction ──────────────────────────────────────────────────────────

FENCED_BLOCK_RE = re.compile(r"```.*?```", re.DOTALL)
INLINE_CODE_RE = re.compile(r"`[^`]*`")
QUOTE_LINE_RE = re.compile(r"^>.*$", re.MULTILINE)


def normalize(text: str) -> str:
    """Strip code blocks, inline code, and quoted lines — those don't count as shirking."""
    text = FENCED_BLOCK_RE.sub("", text)
    text = INLINE_CODE_RE.sub("", text)
    text = QUOTE_LINE_RE.sub("", text)
    return text.strip()


def extract_tail(text: str) -> str:
    """Return last paragraph (or ~600 trailing chars).

    If the trailing paragraph is tiny, grab the previous one too — sometimes
    the question is split across a blank line.
    """
    text = text.rstrip()
    if not text:
        return ""
    paragraphs = [p.strip() for p in re.split(r"\n\s*\n", text) if p.strip()]
    if not paragraphs:
        return ""
    tail = paragraphs[-1]
    if len(tail.split()) < _SHORT_TAIL_WORDS and len(paragraphs) >= _MIN_PARAGRAPHS_FOR_GRAB_PREV:
        tail = paragraphs[-2] + "\n\n" + tail
    if len(tail) > _TAIL_MAX_CHARS:
        tail = tail[-_TAIL_MAX_CHARS:]
    return tail


def read_transcript(path: str) -> list[dict]:
    """Read a Claude Code JSONL transcript. Each line is one event."""
    events: list[dict] = []
    try:
        with Path(path).open(encoding="utf-8") as f:
            for raw in f:
                line = raw.strip()
                if not line:
                    continue
                try:
                    events.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
    except OSError:
        return []
    return events


def _message_text(message: dict) -> str:
    """Join all text content blocks of an assistant/user message into one string."""
    content = message.get("content")
    if isinstance(content, str):
        return content
    if not isinstance(content, list):
        return ""
    chunks: list[str] = []
    for block in content:
        if not isinstance(block, dict):
            continue
        if block.get("type") == "text" and isinstance(block.get("text"), str):
            chunks.append(block["text"])
    return "\n".join(chunks)


def last_assistant_text(events: list[dict]) -> str:
    """Find the most recent assistant text. Skips tool_use-only messages."""
    for event in reversed(events):
        msg = event.get("message")
        if not isinstance(msg, dict):
            continue
        if msg.get("role") != "assistant":
            continue
        text = _message_text(msg)
        if text.strip():
            return text
    return ""


def last_user_text(events: list[dict]) -> str:
    for event in reversed(events):
        msg = event.get("message")
        if not isinstance(msg, dict):
            continue
        if msg.get("role") != "user":
            continue
        text = _message_text(msg)
        if text.strip():
            return text
    return ""


# ── classifier ───────────────────────────────────────────────────────────────


def match_shirk(tail: str) -> tuple[str, str] | None:
    """Return (group_name, snippet) of the first shirking pattern that matches the tail."""
    for group, patterns in SHIRK_PATTERNS.items():
        for pattern in patterns:
            m = re.search(pattern, tail, flags=re.IGNORECASE | re.MULTILINE)
            if m:
                return group, m.group(0)
    return None


def has_destructive_context(tail: str) -> bool:
    for kw in DESTRUCTIVE_KEYWORDS:
        if re.search(kw, tail, flags=re.IGNORECASE):
            return True
    return False


def has_business_ambiguity(tail: str) -> bool:
    for marker in BUSINESS_AMBIGUITY_MARKERS:
        if re.search(marker, tail, flags=re.IGNORECASE):
            return True
    return False


def user_asked_open_question(user_text: str) -> bool:
    text = user_text.strip()
    return bool(text) and text.endswith("?") and len(text) < _USER_Q_MAX_LEN


def classify(
    assistant_text: str,
    user_text: str,
) -> tuple[str, str | None, str | None]:
    """Return (verdict, group, snippet).

    verdict ∈ {"ok", "shirk", "guard:<name>"} — guard verdicts are still "ok" for the
    blocking decision, but we log them separately for tuning.
    """
    normalized = normalize(assistant_text)
    tail = extract_tail(normalized)
    if not tail:
        return "ok", None, None

    hit = match_shirk(tail)
    if not hit:
        return "ok", None, None

    group, snippet = hit

    if has_destructive_context(tail):
        return "guard:destructive", group, snippet
    if has_business_ambiguity(tail):
        return "guard:ambiguity", group, snippet
    if user_asked_open_question(user_text):
        return "guard:user_question", group, snippet

    return "shirk", group, snippet


# ── reason message ───────────────────────────────────────────────────────────

REASON_TEMPLATE = (
    'You ended the turn asking "{snippet}" — that\'s ask-instead-of-do.\n'
    "Take the obvious next step yourself:\n"
    "  - if it's running tests / lint / typecheck, just run it;\n"
    "  - if it's finishing the second half of the same task, finish it;\n"
    "  - if it's creating or updating a file, do it.\n"
    "Ask the user ONLY when:\n"
    "  (1) the action is destructive or irreversible "
    "(force push, drop table, prod deploy);\n"
    "  (2) there's genuine ambiguity in business requirements;\n"
    "  (3) the user themselves asked an open question.\n"
    "Otherwise: do the work and report the result, not a question."
)


def build_reason(snippet: str) -> str:
    snippet = snippet.strip().replace("\n", " ")
    if len(snippet) > _SNIPPET_MAX_LEN:
        snippet = snippet[: _SNIPPET_MAX_LEN - 3] + "…"
    return REASON_TEMPLATE.format(snippet=snippet)


# ── main ─────────────────────────────────────────────────────────────────────


def main() -> None:
    raw = sys.stdin.read()
    try:
        data = json.loads(raw) if raw.strip() else {}
    except json.JSONDecodeError:
        sys.exit(0)

    # Loop guard: if we already blocked once this turn, don't block again.
    if data.get("stop_hook_active"):
        sys.exit(0)

    transcript_path = data.get("transcript_path")
    if not transcript_path or not Path(transcript_path).exists():
        sys.exit(0)

    events = read_transcript(transcript_path)
    assistant_text = last_assistant_text(events)
    if not assistant_text.strip():
        sys.exit(0)
    user_text = last_user_text(events)

    verdict, group, snippet = classify(assistant_text, user_text)

    logger = setup_logging()
    logger.info(
        'verdict=%s group=%s snippet="%s" preview="%s"',
        verdict,
        group or "-",
        (snippet or "").replace("\n", " ")[:120],
        assistant_text.strip().replace("\n", " ")[:200],
    )

    if verdict != "shirk":
        sys.exit(0)

    assert snippet is not None  # noqa: S101 — verdict=="shirk" implies a match
    sys.stdout.write(json.dumps({"decision": "block", "reason": build_reason(snippet)}) + "\n")
    sys.exit(0)


if __name__ == "__main__":
    main()
