---
paths:
  - "**/*.{py,ts,tsx,js,jsx,go,rs,java,rb,php,c,cpp,h,hpp,cs,kt,swift,scala,sh,sql}"
---

Type the real shape of data. Don't reach for an escape hatch — `Any`, an untyped map/dict, `object`, `interface{}` — when the data has a knowable structure. And never silence the type checker; fix the type it's complaining about.

This applies hardest at boundaries: anything a function takes as a parameter, returns, or stores as a field must have a concrete type. Untyped data crossing a function boundary defeats the whole point of the type system.

## The forms

**1. `Any` / untyped dict as a return or parameter type.**
```python
# BAD — the caller has no idea what keys exist; every access is a guess
def classify(call) -> dict[str, Any]: ...
# GOOD — define the shape once; the type documents and enforces it
class Classification(BaseModel):
    label: str
    confidence: float
def classify(call: CallRecord) -> Classification: ...
```
Same smell in any language: `any` / `Record<string, any>` (TS), `map[string]interface{}` (Go), `Object` / `Map<String,Object>` (Java).

**2. Silencing the checker instead of fixing the type.**
```python
# BAD
result = parse(x)  # type: ignore        # the error was real; now it's hidden
value = (data as any).field              # TS: casting away the problem
# GOOD — give parse a real return type, or narrow x so the checker is satisfied honestly
```
A `# type: ignore` / `as any` / `@ts-expect-error` is a debt marker. If you must use one, it needs a comment naming the concrete reason (per `comments-why-not-what.md`), and it's a last resort, not a reflex.

**3. Missing return / parameter annotations** in a typed codebase — leaving them off lets `Any` leak in silently.

## When a loose type is fine

Genuinely unstructured data is allowed to be loose: a JSON blob you immediately validate into a model, a programmatically-built query/pipeline, a logging key-value bag, an inline mapping that never leaves the function. The rule is about data with a *known* shape that crosses a boundary — give that a name.

## Why this rule exists

This is the strongest-measured AI type smell: agents introduce the `any` type roughly 9× more often than human developers (≈2.16 vs ≈0.24 additions per PR). It slips through review because the code looks clean and the PR still merges — but every `Any` is a hole where the compiler stops helping and runtime errors move downstream to a caller who trusted the signature. Define the shape; let the type system catch the bug before it ships.
