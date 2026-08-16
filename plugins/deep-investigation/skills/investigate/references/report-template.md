# Findings artifact template

Write to `.work/<topic>/findings.md`. Order matters: the reader gets the answer before the
evidence, and can audit the evidence without asking you anything.

```markdown
# <Subject> investigation (<date>)

## Question
Verbatim quote of what was asked, or an exact restatement.

## Answer
One paragraph. The mechanism, its size, and what it means. No hedging — if something is
uncertain it belongs in "Open", not softened in here.

## How this was established
2–3 sentences: how many hypotheses, what evidence, what was falsified. Enough for a reader
to judge whether to trust the rest.

## Does it add up
| | Value |
|---|---|
| Observed effect | −40% |
| Mechanism A (H3.1) | −28% |
| Mechanism B (H1.2) | −9% |
| Unexplained residual | −3% |

If the residual is large, say so here rather than burying it. This table is the honesty
check.

## Verified mechanisms
Ordered by magnitude. Each: what it is, what it does to the observed thing, the number,
and where the evidence came from (file:line, query, source URL + date).

## Ruled out
Each: what was checked, the number that rules it out, why people expected it to matter.
This section stops the next person from re-checking what you already killed.

## Open
Each: what is still undecided, exactly what data would close it, and why it matters.

## Hypothesis tree
The full tree with final statuses and key numbers inline.

## What follows
Numbered actions, each tied to a verified mechanism and pointing at the code/config/process
that has to change.

## References
Table: file | lines | what it does. Plus external sources with dates.
```
