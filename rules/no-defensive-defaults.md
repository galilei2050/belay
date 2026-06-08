Don't write code that defends against states that cannot happen. Trust the types, the schema, the framework guarantees, and the checks already done earlier in the same function. Fail loud on the states that *can* happen — don't paper over them.

Tell phrases — if you're about to write a check justified by any of these, stop and ask "can this state actually occur, given the types and the code above?":
- "just in case" · "to be safe" · "to be robust" · "if this ever happens" · "for backward compatibility" · "defensive"

If you can't name a concrete, reachable scenario, delete the check.

## The four forms

**1. Catch-all exception handlers.** A bare `except Exception` / `catch (e)` that logs-and-continues or returns a default converts a bug into silent wrong data.
```python
# BAD
try:
    user = db.fetch(user_id)
except Exception:
    user = None          # now every caller gets a lie
# GOOD — let it raise. Catch a SPECIFIC type only when failure is real and recoverable,
# and say so:
try:
    raw = remux(blob)
except (CalledProcessError, TimeoutExpired) as exc:   # documented, narrow, degrades on purpose
    log.warning("remux failed, serving raw", error=str(exc))
    raw = None
```
Acceptable bare catch: only when immediately followed by `raise` (re-tag with context) or a logged, documented, intentional degrade.

**2. Optional parameters "for flexibility".** Don't add `x: T | None = None` unless a real call site passes nothing. Check the call sites first.
```python
# BAD — every caller passes a db; the None branch is dead and lies about the contract
def create_lead(data, db=None): ...
# GOOD
def create_lead(*, data: Lead, db: Database): ...
```

**3. Silent fallbacks that mask missing data.** Returning `[]`, `0`, `""`, or a default object when the real answer is "this shouldn't be empty" hides the failure downstream.
```python
# BAD
config = load_config() or {}        # if load failed, you now run on an empty config
# GOOD — fail fast at the boundary; a missing config is a startup crash, not a runtime mystery
config = load_config()              # raises if absent
```

**4. Redundant guards for impossible states.** If the type says non-null, the router validated it, or line 3 already checked it, don't check again.
```python
# BAD
def handle(user: User):     # already non-null by signature + router schema
    if user is None:
        raise ValueError("user is None")
    process(user)
# GOOD
def handle(user: User):
    process(user)           # if you genuinely want a tripwire, `assert user` beats an if/raise
```

## The positive replacement

- Rely on static types, schema validation, and framework/router invariants. Document the invariant in a one-line comment if it's non-obvious, instead of re-checking it.
- For a precondition you truly want enforced, use `assert` (a tripwire that says "this is impossible"), not an `if/return` (which says "this is a normal branch" and hides the bug).
- Worried about an edge case? Add a *test*, not a runtime guard.

## Why this rule exists

This is the most-reported AI code smell. Models are trained on a corpus full of code that "does not crash," so they default to swallowing errors and adding null guards everywhere; agents introduce the `any`/catch-all/optional pattern far more often than human authors, and ~45% of AI-generated snippets ship with at least one vulnerability that defensive-looking code masks rather than fixes. Defensive defaults don't catch bugs — they hide them until a user finds them in production, with no stack trace. Loud failure with a clear trace is cheaper than silent wrong data every time. Pairs with `no-speculative-generality.md` (optionals are speculative) and `root-cause-not-symptom.md` (the catch-all is the #1 band-aid).
