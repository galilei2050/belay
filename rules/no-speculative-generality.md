---
paths:
  - "**/*.{py,ts,tsx,js,jsx,go,rs,java,rb,php,c,cpp,h,hpp,cs,kt,swift,scala,sh,sql}"
---

Build exactly what the current task needs — nothing for a future that hasn't arrived. No parameter without a caller, no abstraction with one implementation, no config flag for a single use, no extension hook nobody asked for. YAGNI.

Tell phrases: "for flexibility" · "to make it extensible" · "future-proof" · "in case we later need" · "to support other X down the line". Each one is a request to add code you can't justify with a real, present requirement.

## The forms

**1. Parameters no caller sets.**
```python
# BAD — every caller uses the defaults; the knobs are dead weight that lies about the contract
def render(data, *, theme="light", retries=3, fmt="html", strict=False): ...
# GOOD — take what's actually used; add a parameter the day a second caller needs it
def render(data): ...
```

**2. Abstractions with one implementation.** An interface/base class/strategy/factory wrapping a single concrete thing.
```python
# BAD — one implementation hiding behind a protocol "in case we swap it"
class StorageBackend(Protocol): ...
class S3Storage(StorageBackend): ...   # the only one, and there is no plan for another
# GOOD — use S3 directly. Introduce the abstraction when the SECOND backend appears.
```

**3. Config / flags for hypothetical variation.** A setting with exactly one value it's ever set to.

**4. Pass-through layers.** A wrapper that only forwards to the thing it wraps, added "to decouple."

**5. Generality of shape** — accepting `**kwargs`/`...args`/a grab-bag dict so the function "can take anything later." It takes anything wrong, today.

## The replacement

Solve the concrete case in front of you, directly, with the simplest construct the language offers. When the second case actually arrives, *then* generalize — you'll know the real axis of variation instead of guessing it. Refactoring from one concrete case to two is cheap and informed; carrying a wrong abstraction is expensive and forever.

## Why this rule exists

Models are trained on mature, fully-featured codebases and reproduce that maturity even for a fresh, simple task — "Unnecessary Abstraction" is a directly measured ~8% of all smells agents introduce. Premature generality is pure cost: every speculative parameter, layer, and interface is more surface to read, test, and keep correct, in service of a requirement that usually never comes — and when it does, it rarely matches the guess. This is the same instinct as defensive optionals (`no-defensive-defaults.md`): adding code for a state that isn't real. Don't anticipate. Implement what's asked.
