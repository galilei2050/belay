Say it in the fewest words that fully carry the meaning — in code, in comments, in docs, and in your messages to the user. Verbosity wastes tokens, buries the signal, and is a measured AI tell ("overly verbose but somehow still under-documented where it matters").

Concise ≠ terse-to-the-point-of-loss. Keep every load-bearing fact; cut everything that repeats, hedges, or pads.

## In prose (comments, docs, your replies)

Make the point once, directly. Cut preamble, throat-clearing, restatement of the question, and ceremonial wind-up/wind-down.
```
# BAD (verbose) — 7 lines to say one thing
**`Agent` (baski) is abstract; `Assistant` (this app) is concrete.** `baski.agents.Agent`
is a transport-agnostic LLM loop — it knows tools, messages, and tracing, and nothing about
Telegram, this user, or this product. All the concrete knowledge lives in `Assistant`: it
knows it speaks through Telegram, who the owner is, which tools/skills are wired... [continues]

# GOOD (compact) — same content, one line
`baski.agents.Agent` = transport-agnostic LLM loop (tools/messages/tracing).
`Assistant` = the concrete Telegram/owner/prompt layer. Generic → baski; "because Telegram" → Assistant.
```
Tells to cut: "It's worth noting that…", "Basically/Essentially…", "In order to" (→ "to"), "due to the fact that" (→ "because"), sentences that restate the previous sentence, lists where a clause would do.

## In code

The least code that's still clear. Don't pad with intermediate variables used once, redundant helpers, or boilerplate the language doesn't need.
```python
# BAD
result_list = []
for item in items:
    result_list.append(transform(item))
return result_list
# GOOD
return [transform(item) for item in items]
```
This is the readability twin of `keep-it-simple.md` — fewer moving parts, less to read. (But don't compress into cleverness; clarity wins over character-count.)

## In commits / PRs / summaries

Scannable. Bullets over paragraphs, the "what" and "why" without narrating every step you took. The user reads the result, not your travelogue.

## Why this rule exists

Models are trained to be thorough and "helpful," which decays into padding: confirmed higher median function length and verbosity than human code, and a constant forum complaint about wall-of-text output. Padding costs real tokens (the user's money and context budget) and, worse, hides the one sentence that matters inside ten that don't. Respect the reader's attention and the token budget: every word should earn its place.
