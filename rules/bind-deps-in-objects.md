---
paths:
  - "**/*.{py,ts,tsx,js,jsx,go,rs,java,rb,php,c,cpp,h,hpp,cs,kt,swift,scala}"
---

Bind a dependency once, in a constructor — don't thread it through every call. A function that takes a client, a database, a bot, a store, or a config as an argument is a **method on the object that holds it**. A function whose first argument is an entity and that derives a view or a decision from it is a **method on that entity**. What's left — pure helpers over primitives — stays a free function.

The smell is a module that is a *pile of functions sharing a first argument*. That shared argument is the object that wants to exist.

```python
# BAD — the client and the entity are re-threaded at every call site
async def classify(anthropic: AsyncAnthropic, evidence: Evidence) -> Classification: ...
def render(evidence: Evidence) -> str: ...
async def collect(db: AsyncDatabase, *, conversation_id: int, since: datetime) -> Evidence: ...

# GOOD — the dependency is bound once; the entity renders itself
class MessageClassifier:
    def __init__(self, anthropic: AsyncAnthropic) -> None:
        self._anthropic = anthropic
    async def classify(self, evidence: Evidence) -> Classification: ...

class EvidenceCollector:
    def __init__(self, database: AsyncDatabase) -> None: ...
    async def collect(self, *, conversation_id: int, since: datetime) -> Evidence: ...

class Evidence:
    def render(self) -> str: ...        # was render(evidence)
```

## The three forms

**1. A dependency in the parameter list.** A client / db / bot / store / logger / config passed to a
function is a constructor argument in disguise. Every caller has to know about it and carry it, and
the signature stops saying what the call is *about*.
```python
# BAD — the runner holds a database only to hand it back on every call
await claim(database, public_id=pid, fire_at=at)
await reschedule(database, public_id=pid, fire_at=next_at)
await mark_done(database, public_id=pid)
# GOOD
self._tasks = FireStore(database)          # once, in __init__
async with self._tasks.claim(public_id=pid, fire_at=at) as task: ...
```

**2. An entity threaded through outside functions.** A formatter, validator, or predicate whose first
argument is a model belongs on the model — one place to find every view of it.
```python
# BAD                                    # GOOD
_render_signals(classification)          classification.render()
is_expired(task, now)                    task.is_expired(now)
```
Exception: when the derivation belongs to a *different layer* than the entity (a transport-specific
rendering of a domain model), keep it in that layer — but on that layer's own object, not loose.

**3. A module used as a singleton.** Module-level mutable state plus functions that mutate it is a
class with the constructor deleted. It can't be instantiated twice, can't be reset in a test without
reaching into a private global, and its invariants are documented nowhere in particular.
```python
# BAD
_pending: dict[str, Question] = {}
def answer_pending(*, chat_id, text) -> bool: ...
def resolve_tap(payload: str) -> bool: ...
# GOOD — one class, one documented invariant, one module-level instance if a singleton is truly wanted
class PendingQuestions:
    def __init__(self) -> None: self._pending = {}
    def answer(self, *, chat_id, text) -> bool: ...
questions = PendingQuestions()
```

## When a free function is right

Don't wrap everything in a class — an object with no state is ceremony (`keep-it-simple.md`). Keep it
a plain function when it is:
- **pure over primitives** — `clip(text, limit)`, `humanize_tokens(n)`, `split_message(text)`;
- a **factory / registration hook matching a framework contract** — `build(deps, id) -> list[Tool]`,
  a route-mounting function, a DI provider;
- a **context manager over ambient state** — `acting_as(actor, run_id=...)` — where an object threaded
  down through every layer would be the worse design.

The test: *does it need a dependency or an entity to do its job?* If yes, it has a home. If it only
needs the values you hand it, leave it alone.

## Why this rule exists

Models emit free functions with long parameter lists because that is what a corpus of scripts,
tutorials, and stdlib examples looks like — and because appending a function is the cheapest edit, so
an agent adding "one more helper" never pays the restructuring cost. The result compounds: the same
client is threaded through four layers, a parameter list grows past the linter's `max-args` and picks
up a `noqa`, and a module becomes a dozen functions sharing a first argument with no single place
owning the invariant. This is the parameter-list half of `keep-it-simple.md` and the discovery half
of `reuse-before-reinvent.md` — behaviour with no home object is behaviour the next agent reimplements
somewhere else. Give the state an owner: call sites get shorter, invariants get one documented place,
and tests get a seam.
