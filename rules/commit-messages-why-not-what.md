A commit message earns its keep by recording what the diff can't: why this change, now. The diff already shows *what* changed and *how*; the subject names the change in one imperative line, the body gives the reason a future reader can't recover from the code — the problem, the constraint, the rejected alternative, the ticket. A message that only re-says the diff records nothing.

Two tests:
- **Diff test:** could `git show` produce your message by translating the patch to English ("update auth.py", "timeout → 30")? Then you're narrating the diff — write the why instead.
- **Amnesia test:** months later someone runs `git blame` and reads only your message. Do they learn anything the code doesn't say? The diff lives in git forever; the reason lives only if you wrote it.

## The forms

**1. The empty subject** — "update files", "fix bug", "wip", "misc", "address review". Says nothing, and in a history search every one embeds to the same noise, so the right commit can't be found.
```
# BAD
fix bug
# GOOD
Fix race in cache refresh: two writers could clobber the freshly-set TTL
```

**2. Narrating the diff** — listing every file/function, restating the patch. The diff is attached; don't transcribe it.
```
# BAD
Edit handler.py, update parse(), delete old_validate(), set timeout 30
# GOOD
Drop client-side validation — the gateway enforces it now, we were checking
twice. Timeout 10→30s: upstream p99 is 12s, healthy requests were timing out.
```

**3. Hallucinated rationale** — "fixes security hole", "improves performance", "resolves #123" with no such bug, benchmark, or issue. Cite only what you have; if the why is just the diff, say what you did and stop. (`ground-claims-in-data.md`)

**4. The change-narration that belongs *here*, not in the code.** The "why we switched X→Y" that `comments-why-not-what.md` bans from a source comment (it goes stale) is exactly right in a commit body (a permanent snapshot of one change).
```
# BAD — source comment, stale on the next edit:
#   now using Redis instead of Memcached
# GOOD — commit body:
Switch cache to Redis: need per-key TTL eviction Memcached's global LRU can't give.
```

## Shape

- **Subject:** imperative, ≤~50 chars (`Add…`/`Fix…`/`Drop…`). Match the repo's log — Conventional Commits if it uses them, plain imperative otherwise (`match-the-codebase.md`).
- **Body** (when the change isn't self-evident): the why in 1–3 sentences, ~72-col wrapped; push deeper detail to the linked ticket/PR/ADR. None for trivial changes — subject only. Measure length in why-not-in-the-diff, not in lines.
- One logical change per commit. Scales to PR descriptions: state the intent, don't re-list the commits.

## Why this rule exists

Models are trained to "summarize the diff," so an agent writing a commit narrates what changed — and the corpus is full of "update file"/"fix bug" messages that commit-generation studies filter out as noise, then the model reproduces them. The cost is measured: commit-quality work scores "what" and "why" separately, the "why" is what judges weight most, and code–comment inconsistency alone rides with a bug-introducing commit ~1.5× more often. There's now a second reader: agents don't sweep `git log` by default, but the moment one asks "why is this here, safe to change?" it runs `git blame`/`git show` and reads your words, while history-aware retrieval embeds subject+body as the searchable record of intent. A real why is recoverable for years; "update files" is gone. Pairs with `comments-why-not-what.md` (the why-this-change that must NOT go in a comment goes here) and `ground-claims-in-data.md`.
