# comment-guard-hook

PreToolUse gate on `Write`/`Edit`/`MultiEdit`. A Python function or class docstring may run to
**3 non-blank lines**. Past that the edit is denied.

## Why a line count and not a rule about content

The failure it exists to stop is the essay: a model asked to write a contract writes the contract
into the docstrings, and sixteen lines of design above a stub reads as finished work long enough
to survive review. Every attempt to catch that by *meaning* — narration, restatement, open
questions, duplicated facts — needs judgement, and a hook cannot judge; regexes match phrases and
miss the point. A line count needs no judgement. It measures, and the only things it excludes are
mechanical: blank lines, trailing comments, and header directives that have no shorter legal form.

What survives the cap is what a docstring is for: **how to call the thing, and what you get back.**

> A docstring should give enough information to write a call to the function without reading the
> function's code. — [Google Python Style Guide](https://google.github.io/styleguide/pyguide.html)

> The docstring for a function or method should summarize its behavior and document its arguments,
> return value(s), side effects, exceptions raised, and restrictions.
> — [PEP 257](https://peps.python.org/pep-0257/)

## What it checks

| | |
|---|---|
| Function / method / class docstrings | capped at 3 non-blank lines |
| Runs of own-line `#` comments | capped the same — otherwise the essay just moves above the quotes |
| Module docstrings | exempt: one orientation paragraph per file has no signature that could carry it |
| Trailing `# …` on a code line | not a block; each annotates its own line |
| Blank lines inside a docstring | not counted, so PEP 257's summary-blank-body shape is free |
| Non-`.py` files | ignored |

Only lines **this edit touches** are judged, by the whole span of the block rather than its first
line — an essay grows by appending to a docstring whose opening quote has not moved. Opening an
old file to change one line is never denied over prose written years ago.

## What the deny says

It names each offender with its length, then gives the ladder: name it better, say it shorter,
and if it still will not fit, the function is doing more than one thing — refactor. Plus where the
overflow actually belongs: CLAUDE.md for rules and decisions, a line comment for one non-obvious
line, the commit message for why this change.

## Scope of an edit

Only prose this edit is answerable for is judged, by the whole span of the block rather than its
first line — an essay grows by appending to a docstring whose opening quote has not moved. A pure
deletion counts as touching the lines it now sits between, so trimming a sixteen-line essay to four
is denied like any other over-cap result. And a file whose previous state did not parse is judged
whole once it parses: otherwise an essay could land beside a syntax error and never be looked at
again.

belay's own sources predate the gate — 59 docstrings across 10 plugins run over the cap. Editing
one of those function bodies stays silent; the deny fires only when an edit rewrites one of those
spans.

## Install

```
/plugin install comment-guard-hook@belay
```

## Tuning

`MAX_LINES` in `hooks/comment_guard_hook.py`.
