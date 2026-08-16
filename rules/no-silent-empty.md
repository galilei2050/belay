---
paths:
  - "**/*.{py,ts,tsx,js,jsx,go,rs,java,rb,php,c,cpp,h,hpp,cs,kt,swift,scala,sh,sql}"
---

Ask the source for the scope you need — don't fetch broadly and filter afterwards. An empty
result must mean "there is genuinely nothing here", never "I asked the wrong question". If
those two cases reach the user as the same blank page, the bug has no symptom at all.

The tell: a collection retrieved, then narrowed in your own code by a field the source itself
accepts as a parameter. Location, tenant, account, date range, department, status — if the API,
the query, or the endpoint takes it, passing it is not an optimization, it's the difference
between a wrong answer and an error.

## The forms

**1. Scope applied as a post-filter instead of in the request.** The source was never company-wide;
the token, the blob, or the endpoint already decided a scope, and your filter runs against data
that could never match.
```python
# BAD — the client is pinned to one shop; filtering by another yields [] forever, silently
orders = shopmonkey.fetch_orders(since=start)
return [o for o in orders if o.location == location]
# GOOD — the scope goes in the request; a source that can't take it is a source you can't use here
return shopmonkey.fetch_orders(since=start, location=location)
```
If four call sites each thread the same scope into a different free function
(`orders_at(items, loc)`, `calls(at_location=)`, `reviews(loc)`), that's the object asking to
exist — see `bind-deps-in-objects.md`. One owner knows how each source scopes itself; the page
passes `location` once and never thinks about it again.

**2. An unknown key that yields nothing instead of raising.** A lookup miss — shop not in the
config, feature not in the registry, id not in the map — must be loud. Returning the empty
neighbourhood of a typo turns a five-minute fix into a data investigation.
```python
# BAD
shop = SHOPS.get(name)          # None → every downstream query returns []
# GOOD
shop = SHOPS[name]              # a name that isn't configured is a crash, with the name in it
```

**3. Empty rendered as a fact.** A page, report, or export that draws zero rows identically
whether the query matched nothing or was never valid. Say which one it is: "no orders in this
period" and "this shop isn't configured" are different sentences, and only one of them is news.

**4. Aggregates over an empty set reported as real numbers.** `avg = 0`, `rate = 0%`,
`total = 0` computed from zero rows is not a measurement — it's a missing measurement wearing a
number, and it will be read as a business fact. Return the absence, not a zero.

## The positive replacement

- Push the scope into the query. Where the source can't take it, that is a finding to state, not
  a filter to write.
- Make the scope's own lookup fail loud (form 2) — then an empty result can only mean "none".
- Give absence its own state at the boundary: a distinct empty-view, a `None`/sentinel the caller
  must handle, or an exception. Not a bare `[]` that flows on.
- Test the wrong-scope case, not just the happy one. A test that asserts `[] == []` passes
  whether the filter works or the query is broken (`honest-tests.md`).

## Why this rule exists

Every other failure leaves a trace — a stack trace, a red test, an error log, a 500. This one
leaves a blank page and an unchanged green build, so it survives the checks that catch
everything else and is found weeks later by a human who happens to know the number should not
be zero. Models reach for the post-filter because it is the smaller local edit: the fetch
already exists, a list comprehension is one line, and it *looks* correct at the call site. The
cost lands one layer away, where nothing distinguishes "no data" from "no query". Pairs with
`root-cause-not-symptom.md` (filtering in the view is the wrong-layer fix) and
`no-defensive-defaults.md` (the same silence, arrived at by writing a fallback instead of by
omitting a parameter).
