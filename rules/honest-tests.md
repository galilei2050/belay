---
paths:
  - "**/*.{py,ts,tsx,js,jsx,go,rs,java,rb,php,c,cpp,h,hpp,cs,kt,swift,scala,sh,sql}"
---

A test must be able to fail when the code is wrong. A test that passes regardless of correctness is worse than no test — it's a green light wired to nothing. When a test fails, the code is guilty until proven innocent: fix the code, never weaken the test to make it green.

## The forms

**1. Asserting that code ran, not that it was right.**
```python
# BAD — proves the function was called, nothing about what it did
mock.assert_called()
assert result is not None
# GOOD — assert the actual values / the full effect
mock.assert_called_with(source="yelp", notify=True)
assert result == ExpectedShape(label="lead", score=0.9)
```
Assert the whole result, not one cherry-picked field — adding or dropping a field in production should break the test.

**2. Mocks that don't match production shape.** A mock returning fewer fields, wrong types, or an impossible value lets the code "succeed" on data it would never actually receive, hiding real bugs.
```python
# BAD — real API returns {id, status, items[]}; mock returns {id}
mock_api.return_value = {"id": "1"}
# GOOD — mock the full, faithful shape
mock_api.return_value = {"id": "1", "status": "open", "items": []}
```

**3. Weakening the oracle to go green** — the purest symptom fix (see `root-cause-not-symptom.md`). All of these are forbidden as a way to make a failing test pass:
- relaxing an assertion (`assertEqual` → `assertTrue(x in result)`)
- widening a tolerance until the wrong number fits
- `skip` / `xfail` / commenting out / deleting the failing case
- editing the expected value to match the (wrong) actual output

A test changes only when the *specification* changed, and then the change is deliberate and explained — never as a reaction to red.

**4. Testing internals instead of behavior.** Asserting a private helper's exact kwargs or a flow's internal dict shape ties the test to mechanics: it breaks on harmless refactors yet stays silent when the real contract breaks. Test at the observable boundary — post the request, assert the response and the persisted/emitted side effects.

## Tells

- A test with no assertion, or only `assert_called()` / `is not None` / `assert True`.
- A diff that edits a test and the code it tests in the same change, where the test got looser.
- `skip`/`xfail` added next to a feature that was supposed to start working.
- A mock return value simpler than the real thing it stands in for.

## Why this rule exists

When the only observable signal is "tests pass," models take the cheapest path to green — and if they can edit the test, weakening the oracle is cheaper than fixing the code (documented reward-hacking; SR-Eval shows apparent pass rates drop sharply once tests are made discriminative, i.e. many "passes" were exploiting weak tests). Faithful, behavior-level assertions with production-shaped mocks are the only version of a test that actually defends against regressions. If a test is green, it should be because the code is right.
