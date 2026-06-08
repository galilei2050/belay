Fix the cause, not the symptom. Before you change a line to make an error go away, state the root cause in one sentence. If you can't name it, you haven't found it — keep reading, don't patch.

The single tell: a change whose only effect is to silence a visible failure signal (a red test, a stack trace, an error log, a flaky run) without restoring the invariant that was actually broken.

## The diagnose-then-fix sequence

1. **Reproduce / locate** — find the exact line and input that produces the failure.
2. **Name the cause** — "the crash happens because upstream creates orders with no items, and this function assumes non-empty." One sentence, names the *upstream* fact, not the symptom.
3. **Fix at the layer that owns the invariant** — usually NOT where the error surfaced.
4. **Only then** decide if a guard at the failure site is also warranted.

If the task is "make this test pass" or "make this error go away", silently rewrite it in your head to "find out why this fails and correct that." The first framing is the one that produces band-aids.

## The six band-aid forms — each BAD, with the real fix

**1. Special-casing the failing input** — hard-coding the value from the failing case.
```python
# BAD — pins the code to a test fixture forever
if user_id == "test-user-123":
    return []
# GOOD — fix the logic that produced a wrong user_id, or handle the general empty case
```

**2. Blanket exception swallowing** — catching broad, returning a default.
```python
# BAD — turns a logic bug into silent wrong data
try:
    return compute(order)
except Exception:
    return None
# GOOD — let it raise; if a specific failure is truly recoverable, catch THAT type and document why
```

**3. Retry / sleep / timeout masking a deterministic error** — wrapping a non-transient failure in attempts.
```python
# BAD — "invalid credentials" will fail all 5 times; this just adds 4s of latency
for _ in range(5):
    try: return call_api()
    except Exception: time.sleep(1)
# GOOD — retries are ONLY for genuinely transient failures (network blip, 503). Fix the config/auth.
```

**4. Weakening the test instead of the code** — see `honest-tests.md`. Relaxing an assertion, widening a tolerance, `skip`-ing, or editing the expected value to match wrong output is the purest symptom fix. The test was the only thing telling the truth.

**5. Wrong-layer fix** — guarding in the UI/boundary for a data/domain bug.
```
# BAD — data layer returns a duplicate; you add a dedupe filter in the React component
# GOOD — fix the query/write that creates the duplicate. The UI is not where the invariant lives.
```
If the same guard has to be added in three call sites (shotgun surgery), that's proof the fix belongs one layer up, in the shared source.

**6. Cosmetic no-op** — adding a log line, a comment, or a redundant check as the *only* response to a failure. It looks responsive; it changes nothing.

## Tells in your own explanation (catch yourself)

- "this simple fix adds a guard to prevent the exception" — guard against *what*, caused by *what*?
- "now the test passes" / "the error no longer appears" — with no causal sentence before it.
- "I'm not sure why this happens, but this prevents it" — then you are not done.
- referencing a test's specific input ("the test uses id X, so we handle X") — overfitting.

A root-cause explanation instead reads: *symptom → traced cause → fix at the cause → why the fix is complete.*

## Why this rule exists

Models are trained to turn red signals green; RLHF and verifiable-reward setups reward the smallest change that flips a failing test, with no penalty for shallowness (METR 2025 documents this as reward hacking). The result is measured: LLM-generated code shows ~81% more implementation smells and ~64% more design smells than human baselines, concentrated in shotgun surgery, scattered guards, and overbroad exception handling. Band-aids also recur — the same module gets patched again and again because the cause was never removed. Spend the diagnosis cost once.
