---
name: investigate
description: Answer a "why is X the way it is?" question by building a full hypothesis tree first, then falsifying branches with narrow evidence-gathering subagents until one mechanism is proven to the size of the effect. Use for metric anomalies, incidents, regressions, contradictory data, "why did N drop/spike", "разберись почему", "докопайся" — any question where "probably X" is not an acceptable answer.
---

# Investigate

A question is not a bug report. This is for the case where you must *explain* something —
a number, a behavior, a regression, a contradiction — and a plausible-sounding answer is
worse than none, because someone will act on it.

The method: **enumerate every candidate explanation before checking any of them, then kill
branches with evidence until what survives accounts for the whole effect.**

## When to run this (and when not)

Run it when: the observed thing contradicts intuition or another signal · several
explanations compete and you catch yourself about to write "probably" · the definition of
the thing being measured is itself suspect · someone will make a decision from your answer.

Don't run it when a single obvious cause is visible in one file and the fix is five
minutes. This process costs several subagent dispatches. Say "this doesn't need an
investigation, the cause is <X> at <file:line>" and just fix it.

## Step 0 — Freeze the question and the closing criterion

Write down, verbatim, what is being asked. Then write what would count as an answer:
**a named mechanism plus a number that accounts for the observed magnitude.**

Without the criterion you will stop at the first plausible story. With it, you stop when
the arithmetic closes (Step 7).

## Step 1 — Read the definition before you read the data

Whatever the question is about — a metric, an endpoint, a job, a rate — find where it is
*defined* and read that first: the compute function, the query, the config, the spec.

Field semantics come from code, never from names. `createdAt` means "when the row was
inserted", not "when the event happened", until the code says otherwise. A whole
investigation can end here: the thing measures something other than what everyone assumed.

## Step 2 — Build the whole tree before the first check

Write `.work/<topic>/hypotheses.md`. Root question, branches that scope, leaves that are
falsifiable:

```
Q0: Why does X show Y?
├── Q1: Is the measurement wrong?
│   ├── H1.1: definition/scope mismatch (numerator and denominator disagree)   [untested]
│   └── H1.2: collection bug — dedup, filter, attribution window               [untested]
├── Q2: Did the input change?
│   ├── H2.1: volume shift                                                     [untested]
│   └── H2.2: composition shift — same volume, different mix                   [untested]
├── Q3: Did the system change?
│   ├── H3.1: a deploy/config/schema change at the right date                  [untested]
│   └── H3.2: a dependency or infra change                                     [untested]
├── Q4: Did the outside world change?
│   ├── H4.1: seasonality / market / upstream provider                         [untested]
│   └── H4.2: a competitor/platform/policy event                               [untested]
└── Q5: Is the measurement right and the reality is genuinely that?
    └── H5.1: real degradation, no artifact                                    [untested]
```

Those five branches are the default spine — instantiate all five for any domain, then add
domain-specific ones. Every leaf gets an ID and `[untested]`.

**MECE check before you proceed:** ask "what explanation would a skeptic name that has no
home in this tree?" Add it. A tree missing the true cause cannot find it, and every later
step will look rigorous while being wrong.

**Every mid-investigation fork is two hypotheses, not a question for the user.** If you
find yourself writing "this could be A or B — which should I dig into?", stop: add A and B
as leaves and check both. The user sees resolved findings, not your forks.

## Step 3 — Order by cost, eliminate families early

1. Read code / grep — free
2. Local counts, distributions, log greps — cheap
3. Heavy aggregations, joins, cross-system — moderate
4. Quantitative analysis (correlations, lags) — moderate
5. External lookups (web, third-party APIs) — expensive

If reading one filter in the code kills all of Q1, do that before any query. Kill families,
not leaves.

## Step 4 — One subagent, one slice

Dispatch `evidence-collector` for local facts (code, files, data, logs, git history) and
`web-researcher` for facts that live outside the system. Send several in parallel when the
slices are independent.

Hard rules:

- **One hypothesis per dispatch.** A prompt covering H2.1–H4.5 comes back as a summary
  instead of data, and you cannot audit a summary.
- **Ask for raw output, forbid conclusions.** "Return counts grouped by X for range Y. No
  analysis." The orchestrator — you — is the only one who synthesizes.
- **Name the hypothesis ID in the prompt** so the returned evidence files itself.

## Step 5 — Status the leaf the moment its evidence lands

Replace `[untested]` with one of, and put the key number inline next to it:

| Status | Means |
|---|---|
| `[VERIFIED]` | real mechanism, magnitude measured |
| `[FALSIFIED]` | ruled out by evidence |
| `[NOT A FACTOR]` | mechanism exists, too small to matter — state its size |
| `[PARTIAL]` | real, explains only part of the gap — state which part |
| `[OPEN]` | evidence insufficient; state exactly what data would close it |

Never leave a leaf `[untested]` after its evidence returned. `[FALSIFIED]` is a finding and
ships in the report — it is what stops the next person re-checking it.

## Step 6 — Try to kill what survived

Every `[VERIFIED]` and `[PARTIAL]` leaf goes to `hypothesis-falsifier` before it can be
called a finding. A hypothesis that has not survived a deliberate attempt to destroy it is
a story, not a result.

If the falsifier returns KILLED, restatus the leaf and go back to Step 3 with the branches
still standing. If WEAKENED, downgrade to `[PARTIAL]` or `[OPEN]` and record what it would
take to settle it.

## Step 7 — Make the arithmetic close

The stopping test, and the difference between this and a plausible narrative:

> Do the verified mechanisms, at their measured sizes, add up to the observed effect?

Observed −40%, and your one verified mechanism accounts for −12%? **The tree is
incomplete.** Do not report "the main cause is H3.1". Go back to Step 2, add branches for
the missing −28%, and keep going. Report the residual explicitly when you genuinely cannot
close it: "mechanisms account for 31 of the 40 points; 9 points unexplained, would need
<data>."

## Step 8 — When the user hands you a new fact, walk the tree back

User-stated facts ("we shut that campaign off in July") are accepted as facts. Claims
*derived* from them ("so that's why the number moved") are new hypotheses, `[untested]`.

When a new fact contradicts an earlier assumption, find every leaf that depended on it and
restatus. The tree exists so these dependencies are visible instead of forgotten.

## Step 9 — Report

Write the synthesis to `.work/<topic>/findings.md` using
[references/report-template.md](references/report-template.md). Keep the full tree with
statuses in it — the tree is the audit trail that makes the conclusion checkable.

## External facts: which tool

In priority order, using what is actually available in the session:

1. **Perplexity MCP** (`mcp__plugin_perplexity_perplexity__perplexity_ask` / `_research`) —
   best for "what is the known mechanism / benchmark / did something happen in this
   market". `_research` for multi-source depth, `_ask` for a cited one-shot.
2. **Built-in `WebSearch` / `WebFetch`** — when you need the primary page itself.
3. **SerpAPI** via `scripts/serp_search.py` — only when you need the raw result list
   (ranking, what a query actually returns) rather than a synthesized answer. Requires
   `SERPAPI_API_KEY` in the environment; the script fails loudly if it is absent. Check
   with `python3 ${CLAUDE_PLUGIN_ROOT}/scripts/serp_search.py --check` before planning
   around it.

Never let an external claim in without a source and a date next to it in the tree.

## Quantitative evidence

The moment the question involves two numeric series ("did A cause B?"), read
[references/quantitative.md](references/quantitative.md) before computing anything. Raw
same-period correlation is the single most common way these investigations produce a
confident wrong answer.

## Anti-patterns

**Handing the fork to the user.** "Возможно A, возможно B — что копать?" Both go in the
tree; you check both.

**The mega-prompt.** One subagent, many hypotheses → a summary you cannot audit.

**Hedging where a number belongs.** "probably", "скорее всего", "usually" about anything
checkable is a query you skipped. Mark the leaf `[untested]` and dispatch it.

**Stopping at the first VERIFIED.** One confirmed mechanism is not the answer until Step 7
says it accounts for the effect.

**Fixing one side of a scope mismatch.** If a ratio has one scope on top and another on the
bottom, correcting only one side yields a number that is less wrong and still dishonest.
Restate the scope, then derive both sides together — or split it into two honest measures.
When you find one scope mismatch, audit its siblings: they come in groups.
