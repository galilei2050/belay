Change only what the task requires. Touch the files the task is about, make the smallest diff that does the job, and leave everything else exactly as you found it. A reviewer should be able to read your whole change and see nothing that isn't the task.

## The forms

**1. Touching files you weren't asked to.** The agent edits adjacent modules, configs, or unrelated components "while it's in there," and the user is left diff-hunting to see what else broke. Stay in scope. If a change genuinely requires touching another file, that's fine — but it must be *required*, not incidental.

**2. Ripping out / rewriting working code.** Deleting or rewriting code that already works because you'd "simplify" or "clean it up" — undoing other people's fixes as a side effect. Don't. If working code has a real problem, name it (`surface-the-smell.md`); don't silently replace it.
```
# BAD — asked to add a feature; you also rewrite an unrelated working function "to improve it"
# GOOD — add the feature. Leave the working function alone. Mention it separately if it smells.
```

**3. Rewriting a whole file to change one function.** Produces a diff nobody can review, hiding the one real change among cosmetic churn. Edit the lines that need editing.
```
# BAD — change one function → reformat + reorder + rename across the entire 400-line file
# GOOD — a 6-line diff touching exactly the function in question
```

**4. Smuggling unrelated changes.** Reformatting, renaming, dependency bumps, "drive-by" refactors bundled into a task diff. Each one separately may be fine; mixed into an unrelated change they bloat review and obscure the real edit. Raise them separately. (This is the diff-discipline twin of `surface-the-smell.md` rule 4.)

## The test

Before finishing, read your own diff as if reviewing it. For every changed line ask: "does the task require this line?" If the answer is no — revert that line. Cosmetic churn, opportunistic edits, and collateral changes all fail this test.

## Why this rule exists

Over-broad edits are among the most-cited agent complaints — "happily touches files I didn't ask, then I'm diff-hunting", "undoes previous fixes because it 'simplified' something", "rewrites the whole file so the diff is impossible to review." Large unscoped diffs are the review bottleneck that erases the speed AI was supposed to add, and collateral edits are how working code silently regresses. A tight, legible diff is faster to review, safer to merge, and trivial to revert. Keep the blast radius equal to the task.
