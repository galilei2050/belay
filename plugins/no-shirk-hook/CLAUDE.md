# CLAUDE.md — no-shirk-hook

How to extend `hooks/no_shirk_hook.py` without breaking it.

## Scope (don't expand it)

This hook decides one thing: *does the agent's last message end with an
ask-instead-of-do question that shouldn't be there?* It does NOT:

- judge code quality,
- check that tests passed,
- read git state,
- call an LLM,
- inspect tool-use blocks.

If a feature needs any of the above, it's a different plugin.

## Three knobs and only three

1. **`SHIRK_PATTERNS`** — regexes that match in the *tail* of the agent's last
   message. Group them semantically (`ask_to_run`, `want_me_to`, …). Add RU
   and EN pairs together — split coverage rots fast. Anchor on word boundaries
   (`\b`) or end-of-string (`\s*\??\s*$`); avoid unanchored `.*` matches that
   leak into mid-text.
2. **`DESTRUCTIVE_KEYWORDS`** — false-positive guard №1. If the tail mentions
   any of these, asking is legitimate. Err on the side of adding terms: a
   missed shirk is cheaper than a wrongful block on a force-push question.
3. **`BUSINESS_AMBIGUITY_MARKERS`** — false-positive guard №2. Phrases that
   indicate a tradeoff or named-alternative question that humans must own.

Everything else is plumbing.

## How to add a new shirking phrase

1. Find the closest existing group (or create one — small groups beat one giant
   regex). Add the RU pattern and the EN pattern together.
2. Write tests in `tests/test_no_shirk_hook.py`:
   - **Positive:** the phrase, in a realistic-looking closing paragraph,
     triggers `match_shirk(...)`.
   - **Negative:** the same words used mid-text, in code, or as a quote do
     NOT trigger via `classify(...)`.
3. Run `pytest plugins/no-shirk-hook/ -q` and `ruff check plugins/`.

## How to fix a false-positive (the hook blocked a legitimate turn)

Read the log line in `~/.claude/logs/no-shirk-hook.log`: it shows the matched
group and snippet. Then, in order of preference:

1. **Add a guard term**, not weaken the pattern. If the agent was asking before
   a destructive action, add the destructive verb / object to
   `DESTRUCTIVE_KEYWORDS`. If it was a tradeoff question, add the marker to
   `BUSINESS_AMBIGUITY_MARKERS`.
2. **Tighten the pattern** only if it was overbroad (e.g. matched a substring
   that wasn't a question). Re-anchor with `\b` or `$`.
3. **Last resort:** add a dedicated negative test capturing the phrasing, then
   refactor the pattern so the test passes without breaking other positives.

Don't add per-project allowlists. The hook is project-agnostic.

## Why the tail-only rule matters

Shirking is structural: it's the *closing move* of a turn. A passing mention of
"should I run" inside a long explanation is fine — the agent is reasoning, not
shirking. Restrict matching to `extract_tail()`'s output (last paragraph or
~300 trailing chars). If a new pattern needs full-text matching to work, it's
probably catching the wrong thing.

## Why we don't call an LLM

A Stop hook runs synchronously between turns. An LLM call adds 1-5 seconds of
latency to every single message and costs money on every turn. Regexes give
us deterministic, fast, debuggable behavior. If false-positive/negative rates
ever justify it, a hybrid filter (regex → LLM for ambiguous tail) is an
*addition*, not a replacement.

## Testing

```
uv run pytest plugins/no-shirk-hook/ -q
uv run ruff check plugins/no-shirk-hook/
uv run mypy plugins/no-shirk-hook/hooks/no_shirk_hook.py
```

The `write_transcript` fixture builds a tiny JSONL file in `tmp_path`; tests
hit `main()` through a monkeypatched stdin payload pointing at it. No real
Claude Code session is needed.

## What this file is not

Not a tutorial. It assumes you've read `README.md` and skimmed
`no_shirk_hook.py`. If something is unclear, the source wins.
