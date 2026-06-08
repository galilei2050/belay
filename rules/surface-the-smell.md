---
paths:
  - "**/*.{py,ts,tsx,js,jsx,go,rs,java,rb,php,c,cpp,h,hpp,cs,kt,swift,scala,sh,sql}"
---

When you touch code that already smells, don't quietly build on top of it. Name the smell and either fix it (if it's in scope and cheap) or flag it explicitly. Adding clean code beside rotten code without a word is how debt compounds invisibly.

This is the counter-habit to the default agent posture: "add new code, leave the surrounding mess untouched, say nothing."

## What this means in practice

**1. Propose the refactor you can see.** If implementing the task would be cleaner after a small restructure, say so before piling on.
```
# BAD — asked to add a 4th branch to a 60-line if/elif chain; you add branch #4 and move on
# GOOD — "this chain is now 4 near-identical branches; I'll extract a dispatch table, then add the case"
```
Decompose-then-implement beats implement-onto-mess. State the smell, state the fix, then do it.

**2. Don't silently change a public signature.** Widening, reordering, or making a parameter optional on an existing public function breaks callers you may not see.
```
# BAD — quietly add an optional param to a shared method to make your one call site work
# GOOD — add a new method, OR flag "this needs a signature change affecting N callers" and confirm
```
Add new surface rather than mutating existing contracts; if a contract really must change, make it explicit, not incidental.

**3. Leave a pointer when you can't fix it now.** Out of scope or too risky this turn? Surface it — in your summary to the user, or a `TODO` that names the cause and the fix, not a vague "fixme." Silence reads as "all clean."

**4. Don't smuggle unrelated changes.** Reformatting, renaming, or "while I'm here" edits scattered through a diff hide the real change and bloat review. Keep the diff to the task; raise the cleanup separately.

## Tells

- A diff that adds the Nth copy of a pattern the surrounding code already shows N-1 times (→ `reuse-before-reinvent.md`).
- New code that routes around a bug instead of touching it (→ `root-cause-not-symptom.md`).
- A public function's signature changed in passing, with no mention of who calls it.
- A turn that ends "done" while leaving a smell you clearly saw and said nothing about.

## Why this rule exists

Agents reliably add rather than restructure — studied agentic refactoring is dominated by trivial annotation changes (>91% for some agents), and models "fail to discover" refactors unless explicitly told, defaulting to adding code while leaving visible smells in place. A propose-then-implement step measurably improves this. The cost of silence is a codebase that accretes layers nobody chose: each change locally reasonable, the whole steadily worse. Seeing a smell and not naming it is itself the smell.
