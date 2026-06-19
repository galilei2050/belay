#!/usr/bin/env python3
"""Stop hook: block the agent from ending a turn with an ask-instead-of-do question.

Reads the conversation transcript, looks at the *last* assistant text message,
and if its closing paragraph matches a shirking pattern (and no false-positive
guard fires) returns {"decision":"block","reason":"..."} so Claude Code
re-enters the agent and forces it to do the next obvious step.

One job. No project knowledge, no network, no LLM.
"""

from __future__ import annotations

import contextlib
import gzip
import json
import logging
import re
import sys
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import IO, NamedTuple


class Match(NamedTuple):
    """A successful shirk-pattern hit on the tail of the assistant text."""

    group: str
    snippet: str


class Classification(NamedTuple):
    """Result of classifying an assistant turn.

    ``verdict`` is one of ``"ok"``, ``"shirk"``, ``"guard:<name>"`` — guard verdicts
    are still passthrough for the block decision but logged separately.
    """

    verdict: str
    group: str | None
    snippet: str | None


HOME = Path.home()

_TAIL_MAX_CHARS = 600
_USER_Q_MAX_LEN = 200
_SNIPPET_MAX_LEN = 160
_SHORT_TAIL_WORDS = 5
_MIN_PARAGRAPHS_FOR_GRAB_PREV = 2

# ── shirking patterns (RU + EN, case-insensitive, matched on the tail) ───────

SHIRK_PATTERNS: dict[str, list[str]] = {
    # Listed first so a read-only "want me to look?" offer is tagged here (not as want_me_to):
    # looking is never the destructive act, so this group bypasses the destructive guard
    # (see _firing_guard) — when info is needed, just gather it and report the finding.
    "offer_to_investigate": [
        r"\b(хочешь|хотите)\b[\s,—–-]+(я\s+)?(посмотр|погляж|глян|провер|пров|загляну|чекн|изуч)\w*",
        r"\bмогу\s+(посмотреть|проверить|глянуть|заглянуть|изучить)\b",
        r"\bwant\s+me\s+to\s+(check|look|take\s+a\s+look|inspect|verify|review|see|investigate)\b",
        r"\b(should\s+i|do\s+you\s+want\s+me\s+to)\s+(check|look|verify|inspect|investigate)\b",
        r"\bi\s+can\s+(check|look|take\s+a\s+look|verify|investigate)\b[^?]{0,40}\bif\s+you",
    ],
    "ask_to_run": [
        r"\bзапустить\s*\??\s*$",
        r"\bпрогнать\s+(тесты|линт|typecheck)\s*\??\s*$",
        r"\bshould\s+i\s+run\b",
        r"\bwant\s+me\s+to\s+run\b",
        r"\brun\s+(the\s+)?tests\s*\??\s*$",
    ],
    "want_me_to": [
        r"\bхотите[\s,—–-]+я\s+\w+(ю|у)\b",
        r"\bхочешь[\s,—–-]+(я\s+)?\w+(ю|у)\b",
        r"\bесли\s+хочешь[\s,—–-]+(я\s+)?\w+(ю|у)\b",
        r"\bwant\s+me\s+to\b",
        r"\bwould\s+you\s+like\s+me\s+to\b",
        r"\bdo\s+you\s+want\s+me\s+to\b",
        r"\bif\s+you(\s+want|'?d\s+like),?\s+i'?ll\b",
    ],
    "watch_and_report": [
        r"\b(послед(ить|ю|им)|просле(дить|жу))\b.{0,60}\b(доло(жить|жу)|расскаж\w*|сообщ\w*|напиш\w*)",
        r"\b(watch|monitor|keep\s+an?\s+eye\s+on)\b.{0,60}\b(report|let\s+you\s+know|tell\s+you)\b",
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
        r"\bскаж(ешь|ете)\b[\s,—–-]+(я\s+)?\w+(ю|у)\b",
        # Imperative "скажи — <infinitive>?" offer, e.g. "скажи, оформить?" /
        # "скажи — открыть PR?". The infinitive (-ть/-ти) is the tell; [^?]{0,40}
        # keeps the match inside the final question, not spanning an earlier one.
        r"\bскажи(те)?\b[\s,—–-]+\w+(ть|ти)\b[^?]{0,40}\?\s*$",
        r"\bдай(те)?\s+знать,?\s+если\b",
        r"\blet\s+me\s+know\s+if\s+(you('?d)?\s+like|i\s+should)\b",
        r"\blet\s+me\s+know\s+and\s+i'?ll\b",
        r"\b(just\s+)?say\s+the\s+word\b",
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
    "offer_to_act": [
        # Generic "shall I do it?" offer after laying out a reversible plan, e.g. "Сделать сейчас?",
        # "Делать?", "Сделать это?". [^?]{0,25} keeps the match inside the final question.
        r"\bсдела(ть|ю)\b[^?]{0,25}\?\s*$",
        r"\bделать\s*\??\s*$",
        r"\bshall\s+i\s+(do\s+it|go\s+ahead)\b",
        r"\b(should\s+i|do\s+you\s+want\s+me\s+to)\s+go\s+ahead\b",
        r"\bgo\s+ahead\s*\?\s*$",
    ],
    "commit_offer": [
        r"\b(за)?коммит(ить|нуть)\s*\??\s*$",
        r"\b(за)?коммичу\s*\??\s*$",
        r"\b(за)?пуш(ить|у)\s*\??\s*$",
        r"\b(commit|push)(\s+it)?\s*\?\s*$",
        r"\bshall\s+i\s+(commit|push)\b",
        # Infinitive "do the next git step" offers that carry a trailing clause —
        # e.g. "Запушить и открыть PR, чтобы триггернуть деплой?" The patterns above
        # only fire when the verb is the LAST word; these catch the offer + tail clause.
        # [^?]{0,80} keeps the match inside the final question, not spanning earlier ones.
        r"\b(за)?пуш(ить|у)\b[^?]{0,80}\?\s*$",
        r"\bоткры(ть|ваю)\s+(pr|mr|пул[\s-]?реквест|пиар)\b[^?]{0,80}\?\s*$",
        r"\b(open|create|raise)(\s+a)?\s+(pr|mr|pull[\s-]?request|merge[\s-]?request)\b[^?]{0,80}\?\s*$",
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

# Hard-destructive: irreversible history/data ops. Asking before these is ALWAYS legitimate —
# they suppress a block regardless of what else the turn offers.
HARD_DESTRUCTIVE_KEYWORDS = [
    r"force[-\s]?push",
    r"--force\b",
    r"--force-with-lease\b",
    r"\brm\s+-rf\b",
    r"\bdrop\s+table\b",
    r"\btruncate\b",
    r"\bdelete\s+from\b",
    r"\bудал(и(ть|ение)|яю|им)\b",
    r"\bснести\b",
    r"\bснесу\b",
]

# Soft deploy-ish: a downstream/automatic effect (deploy on merge, a release, a migration) that the
# agent often just MENTIONS while offering reversible work. These suppress a block ONLY when the turn
# offers no reversible action — so "deploy to prod?" stays guarded, but "commit, push, PR (then it
# deploys)?" does not. A genuine manual deploy with no reversible step offered is still guarded.
SOFT_DEPLOY_KEYWORDS = [
    r"\bmigration\b",
    r"\bschema\s+change\b",
    r"\bprod\b",
    r"\bproduction\b",
    r"\bdeploy\b",
    r"\brelease\b",
    r"\bпрод(а|е|у)?\b",
    r"\bмиграц\w+",
    r"\bзадеплои(ть|м|шь)\b",
    r"\bвыкладк\w+",
    r"\bв\s+проде\b",
    r"\bна\s+проде\b",
]

DESTRUCTIVE_KEYWORDS = HARD_DESTRUCTIVE_KEYWORDS + SOFT_DEPLOY_KEYWORDS

# Reversible actions the agent should just DO, not ask about — committing, pushing a branch, opening
# a PR. (A force-push is caught by HARD_DESTRUCTIVE_KEYWORDS first, so "push" here is the safe kind.)
REVERSIBLE_OFFER_MARKERS = [
    r"\b(commit|push)\b",
    r"\b(за)?комм(ит|ич)\w*",
    r"\b(за)?пуш\w*",
    r"\b(pr|mr|pull[-\s]?request|merge[-\s]?request|пул[-\s]?реквест|пиар)\b",
    r"\bветк\w+",
    r"\bbranch\b",
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
    """Return the hook's logger, configured once with rotating gzip handler."""
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


def _parse_jsonl(handle: IO[str]) -> list[dict[str, object]]:
    events: list[dict[str, object]] = []
    for raw in handle:
        line = raw.strip()
        if not line:
            continue
        with contextlib.suppress(json.JSONDecodeError):
            events.append(json.loads(line))
    return events


def read_transcript(path: str) -> list[dict[str, object]]:
    """Read a Claude Code JSONL transcript. Each line is one event."""
    try:
        with Path(path).open(encoding="utf-8") as f:
            return _parse_jsonl(f)
    except OSError:
        return []


def _message_text(message: dict[str, object]) -> str:
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


def last_assistant_text(events: list[dict[str, object]]) -> str:
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


def last_user_text(events: list[dict[str, object]]) -> str:
    """Return the text of the most recent user message, or '' if none."""
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


def match_shirk(tail: str) -> Match | None:
    """Return the first shirking pattern that matches the tail (or None)."""
    for group, patterns in SHIRK_PATTERNS.items():
        for pattern in patterns:
            m = re.search(pattern, tail, flags=re.IGNORECASE | re.MULTILINE)
            if m:
                return Match(group, m.group(0))
    return None


def _any_match(patterns: list[str], tail: str) -> bool:
    return any(re.search(p, tail, flags=re.IGNORECASE) for p in patterns)


def has_destructive_context(tail: str) -> bool:
    """True iff the tail mentions any destructive/irreversible action keyword (hard or soft)."""
    return _any_match(DESTRUCTIVE_KEYWORDS, tail)


def offers_reversible(tail: str) -> bool:
    """True iff the tail offers a reversible action (commit / push branch / PR) the agent should do."""
    return _any_match(REVERSIBLE_OFFER_MARKERS, tail)


def has_business_ambiguity(tail: str) -> bool:
    """True iff the tail asks the user to make a business/tradeoff decision."""
    for marker in BUSINESS_AMBIGUITY_MARKERS:
        if re.search(marker, tail, flags=re.IGNORECASE):
            return True
    return False


def user_asked_open_question(user_text: str) -> bool:
    """True iff the last user message is a short open-ended question."""
    text = user_text.strip()
    return bool(text) and text.endswith("?") and len(text) < _USER_Q_MAX_LEN


def _firing_guard(tail: str, user_text: str, group: str) -> str | None:
    # A read-only "want me to look?" offer is never the destructive act — looking is exactly what to
    # just do, even (especially) when a deploy is downstream. So this group skips the destructive guard.
    if group != "offer_to_investigate":
        if _any_match(HARD_DESTRUCTIVE_KEYWORDS, tail):
            return "destructive"
        if _any_match(SOFT_DEPLOY_KEYWORDS, tail) and not offers_reversible(tail):
            return "destructive"
    if has_business_ambiguity(tail):
        return "ambiguity"
    if user_asked_open_question(user_text):
        return "user_question"
    return None


def classify(assistant_text: str, user_text: str) -> Classification:
    """Classify an assistant turn as ok / shirk / guarded."""
    tail = extract_tail(normalize(assistant_text))
    hit = match_shirk(tail) if tail else None
    if hit is None:
        return Classification("ok", None, None)
    guard = _firing_guard(tail, user_text, hit.group)
    verdict = f"guard:{guard}" if guard else "shirk"
    return Classification(verdict, hit.group, hit.snippet)


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
    """Render the actionable block reason shown to the agent, embedding the matched snippet."""
    snippet = snippet.strip().replace("\n", " ")
    if len(snippet) > _SNIPPET_MAX_LEN:
        snippet = snippet[: _SNIPPET_MAX_LEN - 3] + "…"
    return REASON_TEMPLATE.format(snippet=snippet)


# ── main ─────────────────────────────────────────────────────────────────────


def _load_events() -> list[dict[str, object]] | None:
    """Parse stdin payload, apply gate checks, return transcript events.

    Returns None when the hook should exit silently (bad input, loop guard,
    missing transcript). Empty list means transcript loaded but contained
    nothing usable.
    """
    raw = sys.stdin.read()
    try:
        data = json.loads(raw) if raw.strip() else {}
    except json.JSONDecodeError:
        return None
    if data.get("stop_hook_active"):
        return None
    transcript_path = data.get("transcript_path")
    if not transcript_path or not Path(transcript_path).exists():
        return None
    return read_transcript(transcript_path)


def _log_verdict(
    logger: logging.Logger,
    result: Classification,
    assistant_text: str,
) -> None:
    verdict, group, snippet = result
    logger.info(
        'verdict=%s group=%s snippet="%s" preview="%s"',
        verdict,
        group or "-",
        (snippet or "").replace("\n", " ")[:120],
        assistant_text.strip().replace("\n", " ")[:200],
    )


def main() -> None:
    """Stop-hook entry point: read transcript, classify, emit block decision if needed."""
    events = _load_events()
    if events is None:
        return
    assistant_text = last_assistant_text(events)
    if not assistant_text.strip():
        return
    result = classify(assistant_text, last_user_text(events))
    _log_verdict(setup_logging(), result, assistant_text)
    verdict, _, snippet = result
    if verdict != "shirk" or snippet is None:
        return
    sys.stdout.write(json.dumps({"decision": "block", "reason": build_reason(snippet)}) + "\n")


if __name__ == "__main__":
    main()
