# delegation-hook

A `PreToolUse` gate over **how work is handed to a subagent**: it runs in the background, and it
gets a fixed tool-call budget before its tools are cut off.

## What it does

Two failure modes, one hook.

**Blocking spawns.** A foreground subagent freezes the whole session for its run — minutes in
which nothing else moves, including the parts of the task that never needed its answer. The
harness re-invokes the caller when a background agent finishes, so the result arrives without
anyone waiting, and several agents can run at once. Denied at the spawn: dropping
`run_in_background: false` fixes it.

The failure this must not turn into is the wait loop — an agent that spawns in the background
and then polls the task output file has paid the cost of both. The deny text says so; the
notification is the signal.

**Oversized slices.** An open-ended task makes a subagent grind: it keeps exploring, its window
fills with its own tool output, and it answers from that. Each subagent gets **30 tool calls**;
from call 20 it is told what's left, and past 30 every tool is denied, which forces it to answer
from what it has and name what it didn't reach. The spawning agent is handed the same budget as
a rule at spawn time, so it can size the slice instead of discovering the ceiling by hitting it.

| Situation | Decision |
|-----------|----------|
| `Agent` with `run_in_background: false` | **deny** — let it detach; the harness will notify you |
| `Agent` in the background (explicit or default) | *context* — how to slice a task to fit the budget |
| any tool inside a subagent, calls 1–19 | *silent* |
| calls 20–30 | *context* — `N/30 used, K left` |
| call 31 and beyond | **deny** — answer now from what you have |
| any tool in the main thread | *silent* — the budget is a subagent rule |

Only `deny` or a bare `additionalContext` is ever emitted, never `allow`, so this hook can't
override a sibling hook's verdict on the same call.

## Why 30

Measured over 59 real subagent runs on this machine (2026-08-22): median **18** tool calls,
p90 **38**, max **58**. A 30-call budget leaves the median run untouched and cuts the slowest
~19% — which is exactly the population that bogs down. Note this also truncates the longest
[`review-panel`](../review-panel) reviewers (`integration-reviewer` medians 31.5), by design:
a reviewer that reports at 30 is more useful than one still reading at 58.

## Where the counters live

`~/.claude/state/delegation-hook/<agent_id>.calls`, one byte appended per call — parallel tool
calls in one turn run as concurrent hook processes, and an append is the only shape that can't
lose a count to a lost update. There is no end-of-agent hook to clean up after, so a counter
older than 24h is swept on the next agent's first call.

## Install

```
/plugin install delegation-hook@belay
```

## Config

None. The budget is a constant in `hooks/delegation_hook.py`; a project that wants a different
one declines by not installing the plugin (see [PHILOSOPHY](../../docs/PHILOSOPHY.md):
composition over configuration).
