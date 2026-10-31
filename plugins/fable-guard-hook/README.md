# fable-guard-hook

A `PreToolUse` gate that denies file edits **from the main thread** — the session this hook
mirrors is the delegation side of [`delegation-hook`](../delegation-hook): subagents already
can't spawn priced at Fable, and the thread that spawns them has no cheaper `model` to pick,
because it IS the Fable session.

## What it does

For every `Write` / `Edit` / `MultiEdit` / `NotebookEdit`:

| Call | Decision |
|------|----------|
| from the main thread (no `agent_id` in the payload), in-project, outside `.scratch/` | **deny** — delegate to a subagent (`Agent`) or another session (`SendMessage`) |
| from inside a subagent (`agent_id` present) | *silent* — that's the delegation happening |
| under `.scratch/`, from either | *silent* — one-off scripts don't need a subagent |
| outside the project | *silent* — `fs-acl-hook`'s boundary, not this hook's |

Only `deny` is ever emitted, so this hook never overrides a sibling hook's allow/ask on the same
call (same discipline as `branch-guard-hook`).

## Why a proxy, not the model name

The `PreToolUse` payload carries no field naming the session's model — checked against every
hook's payload reads in this repo and the transcript JSONL schema both. `delegation-hook` already
treats "the session is Fable" as a fact about this machine rather than something it reads off a
call; this hook uses the same fact, and the same proxy delegation-hook already relies on for "is
this a subagent": `agent_id` is present only inside one, so its absence means the main thread.

## Install

```
/plugin install fable-guard-hook@belay
```

## Config

None. The boundary is `$CLAUDE_PROJECT_DIR` (cwd fallback) and the exempt dir is `.scratch/` —
both resolved per invocation, matching `branch-guard-hook` and `fs-acl-hook`.
