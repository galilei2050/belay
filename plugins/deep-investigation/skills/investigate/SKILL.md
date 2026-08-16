---
name: investigate
description: Explains a measured effect whose cause is genuinely unknown — a metric that moved, an incident nobody can account for, two sources reporting different numbers for the same thing, a regression that survived the obvious fix. Enumerates every candidate cause as a hypothesis tree before checking any of them, then kills branches with evidence until the verified mechanisms account for the observed size. Use when several explanations compete, when someone will decide something from the answer, or when you catch yourself about to write "probably" — "why did N drop/spike", "почему упало", "что произошло", "разберись почему", "докопайся", conflicting dashboards. NOT for a bug whose cause is visible in a stack trace, a cause the user already named, or code archaeology ("why is this written this way?") that one grep or `git blame` settles — say the cause and fix it.
---

# Investigate

**Enumerate every candidate explanation before checking any of them, then kill branches with
evidence until what survives accounts for the whole effect.**

## Three gates, all checkable before you start

1. **There is an effect with a size** — a number that moved, a rate, a gap between two
   sources, a count of occurrences. Step 7 closes against it; without one there is no
   stopping rule.
2. **The cause is genuinely unknown**, not merely unconfirmed. A stack trace naming the
   line, a cause the user already named, a question one grep settles: known.
3. **Someone acts on the answer**, or several explanations compete.

Fail any gate and don't run this — it costs several subagent dispatches and most of a
context window. Say "this doesn't need an investigation — the cause is `<X>` at
`<file:line>`" and fix it.

## What is yours, what you dispatch

Three things are never delegated: **the tree** (Step 2), **every status change** (Step 5),
**the synthesis** (Steps 7 and 9). A subagent that builds the tree or writes the findings
has replaced this method with its own guess. The subagents cannot write outside
`.work/<topic>/evidence/`, so the tree and the report are yours by construction.

Read yourself: the definition (Step 1) and anything whose exact wording you must reason
over. Dispatch: anything where the raw output is bulky or the search space is wide — counts,
distributions, log sweeps, external facts. The criterion is context, not difficulty. If the
evidence is 200 lines and the answer is 3, dispatch it.

## Step 0 — Freeze the question, and measure the effect yourself

Write down verbatim what is asked. Then **re-derive the magnitude from the primary source**
— you close against the number you measured, not the one you were handed. A dashboard
timezone, a changed threshold, or someone who just started looking produces a perfectly
rigorous investigation of a non-event.

## Step 1 — Read the definition before you read the data

Find where the subject is *defined* and read that first: the compute function, the query,
the config, the spec. Field semantics come from code, never from names — `createdAt` means
"when the row was inserted" until the code says otherwise. An investigation can legitimately
end here.

For a behavior or an incident, the definitional read is **reproducing it and recording the
exact trigger**. Do that before enumerating causes.

For a ratio, check that numerator and denominator share a scope. A paid-only numerator over
a paid+organic denominator is already broken, and fixing one side leaves it dishonest —
restate the scope and derive both sides together, or split it in two. Scope mismatches come
in groups; check the sibling metrics.

## Step 2 — Build the whole tree before the first check

Write `.work/<topic>/hypotheses.md`. Root question, branches that scope, leaves that are
falsifiable:

```
Q0: Why does X show Y?
├── Q1: Is the measurement wrong?
│   ├── H1.1: definition/scope mismatch                                        [untested]
│   └── H1.2: collection bug — dedup, filter, attribution window               [untested]
├── Q2: Did the input change?
│   ├── H2.1: volume shift                                                     [untested]
│   └── H2.2: composition shift — same volume, different mix                   [untested]
├── Q3: Did the system change?  (deploy, config, schema, dependency, infra)    [untested]
├── Q4: Did the outside world change?  (seasonality, provider, platform, policy) [untested]
└── Q5: Measurement right, reality genuinely that — real degradation           [untested]
```

Those five branches are the default spine for any domain; add domain-specific ones.

**Then dispatch the completeness check before Step 3 may start.** Send the frozen question
and the tree to one `general-purpose` subagent: *"name three explanations that have no home
in this tree; do not evaluate them."* Append its reply verbatim to `hypotheses.md` and add
what fits. A tree missing the true cause cannot find it, and every later step will look
rigorous — and grading your own tree shares its blind spot.

A mid-investigation fork ("could be A or B") is two leaves, never a question for the user.

## Step 3 — Order by cost, kill families not leaves

Code/grep (free) → counts and distributions (cheap) → heavy aggregations (moderate) →
quantitative analysis → external lookups (expensive). If reading one filter kills all of Q1,
do that first. Dispatch tier 1 as an `evidence-collector` slice the moment the search space
is wider than a file you can name.

## Step 4 — One subagent, one slice

`evidence-collector` for facts reachable from this machine, `web-researcher` for facts that
live on the public web. Several in parallel when the slices are independent. Two rules the
agents can't enforce for themselves:

- **One hypothesis per dispatch.** A prompt covering H2.1–H4.5 comes back as a summary, and
  you cannot audit a summary.
- **Name the hypothesis ID in the prompt** so the returned evidence files itself.

## Step 5 — Status the leaf the moment its evidence lands

Replace `[untested]`, key number inline:

| Status | Means |
|---|---|
| `[VERIFIED]` | real mechanism, magnitude measured |
| `[FALSIFIED]` | ruled out by evidence |
| `[NOT A FACTOR]` | mechanism exists, too small to matter — state its size |
| `[PARTIAL]` | real, explains part of the gap — state which part |
| `[OPEN]` | evidence insufficient — state exactly what data would close it |

Never leave a leaf `[untested]` after its evidence returned; a hedge in your own text
("probably", "скорее всего") means a leaf is `[untested]` and a dispatch is missing.
`[FALSIFIED]` ships in the report.

Sizes must be measured on **disjoint** populations. Two leaves measured independently on
overlapping rows both look large and are the same mechanism twice.

## Step 5b — Find the event

For each `[VERIFIED]` leaf, search the changelog for a recorded decision that would produce
this pattern: git log and deploy history, incident channel, ops docs, campaign and config
changes, vendor status pages. Statistics gives you a consistent hypothesis; a dated event
turns it into a cause.

If nothing is recorded, say so — an unrecorded change is itself a finding (undocumented
intervention, unplanned drift), and structural causes (a definition, an attribution window)
correctly have no entry. Never invent a mechanism to fill the gap.

## Step 6 — Try to kill what survived

Every `[VERIFIED]` and `[PARTIAL]` leaf goes to `hypothesis-falsifier` before it is a
finding. Pass it the current tree with statuses, so it doesn't re-raise leaves you already
killed. Order: the largest-share leaf first, then any leaf whose claimed share exceeds the
residual; the rest are optional.

KILLED → restatus and return to Step 3 with the branches still standing. WEAKENED →
downgrade to `[PARTIAL]` or `[OPEN]` and record what would settle it.

## Step 7 — Close it, or say it isn't closed

**Quantitative subject:** do the verified mechanisms, at their measured sizes, add up to the
observed effect? Observed −40% and one verified mechanism at −12% means **the tree is
incomplete** — go back to Step 2 for the missing −28%. Report the residual explicitly when
you cannot close it: "mechanisms account for 31 of 40 points; 9 unexplained, would need
`<data>`."

**Behavioral subject** (incident, regression, contradiction) — two tests instead of the sum:

- **Counterfactual**: remove the mechanism (revert, flag off, patch) and the effect
  disappears; restore it and it returns.
- **Coverage**: every observed instance is accounted for — including the near-misses where
  the mechanism was present and the effect was not.

Two further rules before you may name a primary cause:

- If an `[OPEN]` leaf could plausibly account for the residual, you are not finished. Get
  the data, or report the question as unresolved.
- If the residual is large and no single new leaf plausibly covers it, add **conjunction**
  leaves (H_i × H_j — the effect needs both) before adding more independent branches.

## Step 8 — When a new fact arrives, walk the tree back

User-stated facts are facts. Claims *derived* from them are new leaves, `[untested]`. When a
new fact contradicts an earlier assumption, restatus every leaf that depended on it.

## Step 9 — Report

Write the synthesis yourself to `.work/<topic>/findings.md` using
[references/report-template.md](references/report-template.md), keeping the full tree with
statuses in it.

## Pointers

- Never run this method before? [references/worked-example.md](references/worked-example.md)
  is one full investigation compressed to a page — the tree, the numbers, the close.
- Two numeric series in play ("did A cause B?") → read
  [references/quantitative.md](references/quantitative.md) before computing anything. Raw
  same-period correlation is how these investigations produce a confident wrong answer.
- External facts go to `web-researcher`, which owns the source order. Your rule: nothing
  enters the tree without a source and a date beside it.
