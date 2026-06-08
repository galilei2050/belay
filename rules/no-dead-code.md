---
paths:
  - "**/*.{py,ts,tsx,js,jsx,go,rs,java,rb,php,c,cpp,h,hpp,cs,kt,swift,scala,sh,sql}"
---

Ship only code that runs. No unused imports, variables, functions, or parameters; no unreachable branches; no commented-out blocks; no `TODO`/placeholder/stub left where real code belongs. Every line in the diff must be reachable and necessary.

"Leave NO placeholders or missing pieces — ensure the code is complete" is one of the most common rules in AI-coding rule collections, because the default is to leave scaffolding behind.

## The forms

**1. Unused symbols.** Imports you added and didn't use, a variable assigned and never read, a helper nobody calls, a parameter no branch touches. Delete them. (A linter catches most — run it; see `match-the-codebase.md`.)
```python
# BAD
import os, json, re      # only json is used
result = compute()        # never read again
# GOOD — import json; and either use result or don't bind it
```

**2. Unreachable code.** A branch after an unconditional `return`/`raise`, a condition that can't be true, a `while True` with a guaranteed first-iteration exit.

**3. Commented-out code.** Delete it — version control remembers. (Also in `comments-why-not-what.md`.)
```python
# BAD
# old_way = legacy_call(x)
# return old_way
return new_call(x)
```

**4. Placeholders and stubs presented as done.** `# TODO: implement`, `raise NotImplementedError`, `pass  # fill this in`, a function that returns a hardcoded dummy — none of these are "done." Either implement it or say explicitly it's unfinished (`finish-the-work.md`); never leave a stub silently in a change you call complete.
```python
# BAD — handed back as a finished feature
def calculate_tax(order):
    return 0  # TODO: real calculation
```

## The one exception

A `TODO` is acceptable only when it names a real, out-of-scope follow-up with enough context to act on — and you surface it to the user, not bury it. `# TODO: handle multi-currency once the FX service ships (tracked: #412)` is a pointer; `# TODO: fix this` is litter.

## Why this rule exists

Dead code lies: it tells the next reader "this matters, trace it" when it doesn't, inflating the surface to understand and maintain. Stubs left in a "finished" change are worse — they look like working features until they silently return nothing in production. Agents accrete and scaffold by default and rarely clean up after themselves; removal has to be deliberate. If it doesn't run, it doesn't ship.
