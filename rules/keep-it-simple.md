---
paths:
  - "**/*.{py,ts,tsx,js,jsx,go,rs,java,rb,php,c,cpp,h,hpp,cs,kt,swift,scala,sh,sql}"
---

Write the simplest code that solves the actual problem. Small functions, shallow nesting, short parameter lists, the straightforward algorithm. Complexity has to be earned by a real requirement — never added by default.

This is the most-measured structural failure: agent code carries ~81% more implementation smells and ~64% more design smells than human baselines, with confirmed elevation in cognitive complexity, nesting depth, function length, and parameter-list length. Models default to elaborate because they're trained on mature, elaborate codebases.

## The forms

**1. God function / long method.** A 300-line function that does fetch + parse + decide + persist + notify. Split by concern; the entry point should read as a short list of named steps.
```
# BAD — one function, five concerns, you can't see what it does
def handle(req): ...300 lines...
# GOOD — orchestrator names the steps; each step is its own small function
def handle(req):
    data = parse(req)
    result = decide(data)
    persist(result)
    return result
```

**2. Deep nesting.** Three-plus levels of `if`/`for` is a smell. Use early returns / guard clauses to flatten.
```python
# BAD
def f(x):
    if x:
        if x.ok:
            for i in x.items:
                if i.valid:
                    ...
# GOOD — invert and return early
def f(x):
    if not x or not x.ok:
        return
    for i in x.items:
        if not i.valid:
            continue
        ...
```

**3. Long parameter list.** More than ~4 positional params means the function does too much or wants a struct. Group related args into a typed object (see `concrete-types.md`).

**4. Accretive complexity — layering instead of simplifying.** The reflex to add another condition/branch on top of what's there rather than reworking it. ("Allergic to removing code.") When a fourth case turns a chain into a mess, restructure (and say so — `surface-the-smell.md`), don't pile on.

**5. Naive algorithm where the standard one is known.** O(n²) scans, repeated work in a loop, a chatty call inside a tight loop. Reach for the obvious efficient approach when input can be non-trivial — but don't micro-optimize what doesn't need it (next paragraph).

## Simple ≠ clever, and ≠ premature optimization

Favor readability over micro-optimization unless performance is a stated concern. A clear O(n log n) beats a cryptic bit-twiddling one-liner. The goal is the *least* code and the *fewest* moving parts a competent reader must hold in their head — not the shortest character count, not the most "efficient" thing.

This pairs with `no-speculative-generality.md` (don't add abstraction for the future) and `surface-the-smell.md` (when simplifying needs a restructure, name it). Related smells with weaker evidence in AI code but worth avoiding: feature envy (a method more interested in another object's data than its own), primitive obsession (passing bare strings/ints where a small type belongs), and N+1 queries.

## Why this rule exists

Complexity is where bugs hide and where every future change pays a tax. It's the agent failure mode with the strongest measured signal, and it compounds — one study found agent-driven complexity kept rising month over month. The simplest version that meets the real requirement is almost always the right one; reach for more only when the requirement, in front of you now, demands it.
