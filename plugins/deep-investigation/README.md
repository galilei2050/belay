# deep-investigation

## What it does

Turns "why did X happen?" into a process that cannot stop at a plausible story.

The `investigate` skill makes the agent enumerate **every** candidate explanation as a
hypothesis tree *before* checking any of them, write it to `.work/<topic>/hypotheses.md`,
then knock branches down with narrow evidence-gathering subagents. Two rules do most of the
work:

- **Nothing is a finding until it survives `hypothesis-falsifier`** — an adversarial agent
  whose job is to destroy the conclusion, not to second it.
- **The investigation is not done until the arithmetic closes** — the verified mechanisms
  must add up to the observed effect. A mechanism explaining 12 of 40 points is a partial
  answer, and gets reported as one.

Three subagents ship with it:

| Agent | Does |
|---|---|
| `evidence-collector` | One slice of local fact per dispatch — code, data, logs, git. Returns raw output, refuses to conclude |
| `web-researcher` | External facts with a source URL and date each. Perplexity MCP → WebSearch → SerpAPI, in that order |
| `hypothesis-falsifier` | Six attacks on a confirmed hypothesis: alternative cause, timing, effect size, circularity, counter-case, selection. Returns SURVIVES / WEAKENED / KILLED |

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
- **SerpAPI** — used only when the raw result list is the evidence, and only if
  `SERPAPI_API_KEY` is exported. Check with:
  ```
  python3 plugins/deep-investigation/scripts/serp_search.py --check
  ```
  Without the key the script exits 2 and says so; the investigation proceeds on the other
  tools.
