---
paths:
  - "**/*.{py,ts,tsx,js,jsx,go,rs,java,rb,php,c,cpp,h,hpp,cs,kt,swift,scala,sh,sql}"
---

Search before you write. Before adding a helper, type, constant, or file, grep for one that already exists. Edit what's there; don't grow a parallel copy beside it.

The default failure is invisible: you can't duplicate or reinvent something you never looked for. So look first — every time you're about to introduce a named thing.

## Three forms

**1. Reinventing an existing utility.** A new function identical in purpose to one already in the codebase, in a different place with a different name.
```
# BAD — write format_phone() without checking; the repo already has normalize_e164()
# GOOD — grep for "phone"/"e164"/"format" first; import and use the existing one
```
Rule: before writing `def do_x`, search the codebase for `x` and its synonyms. If something does 80% of it, extend that, don't fork it.

**2. Copy-paste over extract.** Duplicating a block and tweaking one value instead of factoring out the shared logic.
```python
# BAD — the same 8 lines pasted with "yelp" → "google"
def handle_yelp(...):  ...8 lines...
def handle_google(...): ...same 8 lines, one literal changed...
# GOOD — one parametrized helper; the call sites pass the differing value
def handle(source: str, ...): ...8 lines using source...
```
If you're about to paste a block you just wrote, extract it instead. Two copies is the moment to refactor, not the third.

**3. New file over edit.** Creating a second module/router/types file when extending the existing one is smaller and clearer.
```
# BAD — add user_helpers_v2.py next to user_helpers.py; add a second router file in the folder
# GOOD — edit the existing file. A near-synonym filename next to an existing one is a smell.
```
Prefer editing an existing file to creating a new one. Only create a file when the concern genuinely doesn't belong in any existing one.

## Tells

- A new file whose name is a near-synonym of an existing one (`utils2`, `helpers_new`, `*_v2`).
- A block of code that is a copy of another block with one or two literals changed.
- Three or more call sites with the same guard/transform pasted in — extract to one place (this is also how wrong-layer fixes sneak in; see `root-cause-not-symptom.md`).
- Two type definitions with slightly different names for the same shape.

## Why this rule exists

Agents work from a local context window and don't naturally survey the repo before writing, so they reproduce logic that already exists: agent-authored PRs show ~11% higher duplicate-line density (p<0.01) than human PRs, and "duplicate entities" is among the top measured smells in LLM code. Duplication multiplies every future change (fix the bug in all four copies) and reinvention buries the canonical implementation under near-misses. The capability to reuse is there — the discovery step is what's skipped. Make discovery a reflex.
