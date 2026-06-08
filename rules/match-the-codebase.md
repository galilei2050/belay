---
paths:
  - "**/*.{py,ts,tsx,js,jsx,go,rs,java,rb,php,c,cpp,h,hpp,cs,kt,swift,scala,sh,sql}"
---

Before you write, read what's already there and mirror it. New code should look like the code around it wrote it — same patterns, same naming, same idioms, same libraries. The codebase's existing style is the spec; don't invent your own.

This is the single most common rule across every AI-coding rule collection, and a top forum gripe: "every file looks like it was written by a different junior from a different company."

## The forms

**1. Reconnaissance first.** Before adding a feature, grep for how the codebase already does this kind of thing — the existing handler, model, error pattern, test layout — and follow it. Don't reason from the prompt alone as if the repo were empty.

**2. Use the project's own idioms and helpers, not generic ones.** If the repo has a logger, a datetime wrapper, an HTTP client, a base model — use them. Reaching for the raw stdlib / a generic snippet when a project convention exists silently bypasses that convention.
```
# BAD — raw stdlib when the project has a wrapper for exactly this
import logging; logging.getLogger(__name__)
from datetime import datetime; datetime.now()
# GOOD — the project's established way
from app.foundation import Logger, datetime
```

**3. No cross-ecosystem cargo-culting.** Don't import patterns from another language/framework: Java-style AbstractFactory in a small Python script, Node middleware idioms in a Django app, enterprise DI for a toy. Write idiomatic code for *this* stack.

**4. Names that carry meaning, consistent with the repo.** Vague filler names (`processData`, `handleStuff`, `doWork`, `data2`, `tmp`) are a measured AI tell. Name for what the thing *is* in this domain, matching the surrounding naming scheme (camelCase vs snake_case, `get_`/`fetch_`/`load_` conventions).

**5. Don't fight the formatter/linter.** Run the project's formatter and linter; adopt their output. Don't hand-format to your own taste or leave lint errors for the user.

## How to apply

1. Find 1-2 existing files that do something similar to your task. Read them.
2. Copy their structure, their imports, their naming, their error/test pattern.
3. When the existing convention is genuinely bad, don't silently diverge — flag it (`surface-the-smell.md`) instead of starting a second style.

## Why this rule exists

A model works from a local window and its training distribution, not from a mental model of *this* repo, so left alone it emits generically-styled code that's subtly foreign to its surroundings. The cost is a codebase with no single voice: every file a slightly different dialect, so readers can't build reliable intuitions and reviewers burn time on style instead of substance. Consistency is worth more than any individual style preference — match what's there.
