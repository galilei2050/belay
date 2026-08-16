# deep-investigation

## What it does

Turns "why did X happen?" into a process that cannot stop at a plausible story.

The `investigate` skill makes the agent enumerate **every** candidate explanation as a
hypothesis tree *before* checking any of them, write it to `.work/<topic>/hypotheses.md`,
then knock branches down with narrow evidence-gathering subagents. Four rules do most of the
work:

- **The tree gets an outside completeness check** before any evidence is gathered — a fresh
  subagent names explanations the tree is missing, because self-grading a tree you just
  wrote finds nothing.
- **Nothing is a finding until it survives `hypothesis-falsifier`** — an adversarial agent
  whose job is to destroy the conclusion, not to second it.
- **The investigation is not done until it closes**: for a number, the verified mechanisms
  must add up to the observed effect; for a behavior, the mechanism must pass a
  counterfactual and cover every observed instance.
- **A statistical result is not a cause until a dated event is found** in the changelog — and
  a missing entry is reported as a finding, not filled in with a story.

Three subagents ship with it:

| Agent | Does |
|---|---|
| `evidence-collector` | One slice of local fact per dispatch — code, data, logs, git, internal services. Returns raw output, refuses to conclude |
| `web-researcher` | External facts with a source URL and date each. Perplexity MCP → WebSearch → SerpAPI, in that order |
| `hypothesis-falsifier` | Seven adversarial attacks on a hypothesis that already has evidence; returns SURVIVES / WEAKENED / KILLED (see `agents/hypothesis-falsifier.md`) |

Domain-agnostic: a metric anomaly, a regression, an incident, a contradiction between two
dashboards. It is not a debugger — for a bug with one visible cause, just fix the bug.

## Install

```
/plugin install deep-investigation@belay
```

Then ask a why-question, or invoke `/investigate` directly.

## Config

Nothing required. Two optional external-search paths, both detected at runtime:

- **Perplexity MCP** — used when the `perplexity` plugin is installed. Preferred for
  mechanisms, benchmarks, and market events.
- **SerpAPI** — `scripts/serp_search.py`, used only when the raw ranked result list is
  itself the evidence. Needs `SERPAPI_API_KEY` exported; without it the script exits 2 with
  a pointer to the signup page and the investigation proceeds on the other tools.
