# delegation-hook

A `PreToolUse` gate over **how work is handed to a subagent**: it runs in the background, and it
gets a fixed context and wall-clock budget before its tools are cut off.

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
fills with its own tool output, and it answers from that. Each subagent gets **250k tokens of
context and 7 minutes of wall-clock**; from 200k or minute 5 it is told its spend so far, and past
either limit every tool is denied, which forces it to answer from what it has and name what it
didn't reach. The spawning agent is handed the same budget as a rule at spawn time, so it can size
the slice instead of discovering the ceiling by hitting it.

Context is read from the subagent's own transcript (`<session>/subagents/agent-<id>.jsonl`, next to
the parent's `transcript_path`): input plus both cache counters of its latest model call.

**A truncated result belongs to the caller.** The budget reaches the spawner before it chooses the
slice, so an agent that answers half of one hit a ceiling the caller already knew about — the
slicing missed, the agent didn't. The spawn-time rule says so, because a caller that reports it as
the agent's failure ("the agent didn't get to the smoke test") learns nothing and cuts the next
slice the same size, instead of naming the remainder and dispatching a right-sized agent for it.

| Situation | Decision |
|-----------|----------|
| `Agent` with `run_in_background: false` | **deny** — let it detach; the harness will notify you |
| `Agent` in the background (explicit or default) | *context* — how to slice a task to fit the budget, and who owns a short answer |
| any tool inside a subagent, under 200k context within 5 min | *silent* |
| 200k–250k context, or past 5 min | *context* — `Nk/250k tokens of context and M/7 minutes used` |
| over 250k context, or past 7 min | **deny** — answer now from what you have |
| any tool in the main thread | *silent* — the budgets are a subagent rule |

Only `deny` or a bare `additionalContext` is ever emitted, never `allow`, so this hook can't
override a sibling hook's verdict on the same call.

**Model routing.** The spawn-time context also carries a one-liner on picking the model per
slice, because the marketplace agents pin `model: opus` and the spawner can override it with the
`model` param. The routing follows Anthropic's guidance and 2026 subagent-routing consensus:
route by task shape, don't default upward — `opus` for review/falsification/hard debugging,
`sonnet` for writing code and tests at ~half the cost, `haiku` for search and mechanical checks.

## Why context instead of calls, why 250k, and why 7 minutes

The budget limits how big a slice can get; it must not cut an agent that is on track, because a
restart re-reads what the first run already had. Measured over 999 subagents (2026-09/10):

- The old **30-call** cap stopped 47 agents at a median **124k** context — only ~57k above the
  **~67k** a subagent starts with. After 51 of 75 budget stops, a same-type agent was spawned in
  the same session within 30 minutes and re-read a median **59k** tokens. Thirty cheap Greps and
  thirty full-file Reads are not the same spend; tokens are.
- **250k** (the user's number) is above p95 (~200k), so it stops only the bloated tail (5 of 989
  runs) — a drift guard, not a slicing enforcer. Degradation with context is gradual in the
  published evidence (NoLiMa, Chroma "Context Rot"), with no threshold that justifies cutting lower.
  The warning at **200k** nudges the p95 agent to stop reading and write.
- **7 minutes** bounds how long the caller waits: before the hook, runs took median **3.7 min**,
  p90 **14.2 min** (727 runs, 2026-08-22); with it, median 2.2, p90 5.3. It is wall-clock, so a
  machine suspend mid-run spends it. What no budget can catch is an agent stuck inside a single
  hung API request — a hook only runs when a tool is called.

## Where the state lives

`~/.claude/state/delegation-hook/<agent_id>.calls`, one fixed-width timestamp appended per
call — parallel tool calls in one turn run as concurrent hook processes, and an append is the
only shape that can't lose a record to a lost update; the first record is the start time the
wall-clock budget measures against. There is no end-of-agent hook to clean up after, so a
file older than 24h is swept on the next agent's first call.

## Install

```
/plugin install delegation-hook@belay
```

## Config

None. The budget is a constant in `hooks/delegation_hook.py`; a project that wants a different
one declines by not installing the plugin (see [PHILOSOPHY](../../docs/PHILOSOPHY.md):
composition over configuration).
