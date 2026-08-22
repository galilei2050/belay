# delegation-hook

A `PreToolUse` gate over **how work is handed to a subagent**: it must run in the foreground,
and it gets a fixed tool-call budget before its tools are cut off.

## What it does

Two failure modes, one hook.

**Detached subagents.** The `Agent` tool detaches by default, and a detached agent hands the
caller nothing to block on — so the next step is always polling the task output file in a wait
loop. One observed run: 7½ minutes of a `until [ -s …/tasks/xxx.output ]; do sleep 5; done`
loop, and a context full of `tail` output, for a result the foreground call would have returned
directly. Denied at the spawn, where `run_in_background: false` still fixes it.

**Oversized slices.** An open-ended task makes a subagent grind: it keeps exploring, its window
fills with its own tool output, and it answers from that. Each subagent gets **30 tool calls**;
from call 20 it is told what's left, and past 30 every tool is denied, which forces it to answer
from what it has and name what it didn't reach. The spawning agent is handed the same budget as
a rule at spawn time, so it can size the slice instead of discovering the ceiling by hitting it.

| Situation | Decision |
|-----------|----------|
| `Agent` without `run_in_background: false` | **deny** — foreground, or split the work |
| `Agent` with `run_in_background: false` | *context* — how to slice a task to fit the budget |
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
