A comment earns its place only by saying something the code cannot. Explain WHY, never WHAT. If a competent reader of this language could derive the comment by reading the line below it, delete the comment.

Two tests before you keep any comment:
- **The redundancy test:** could a basic parser produce this comment by translating the code to English? ("# increment counter" over `counter += 1`) → delete it.
- **The history test:** would this comment still be true and relevant if every previous version of this code had never existed? If it only makes sense as a diff narration → delete it.

## What to delete

**1. Restating the code** (≈30% of AI comments).
```python
# BAD
# loop over users and check if active
for u in users:
    if u.active:        # check if active
# GOOD — no comment; the code says this already
```

**2. Narration-of-change / changelog comments** (≈18%). The code describes the present, not the journey. Your diff and commit message hold the history — not the source.
```python
# BAD
# now using Redis instead of Memcached
# fixed off-by-one here
# updated per PR #456 / John's suggestion
# removed the old validation
# GOOD — none of these. If a non-ideal choice is *current*, state it as current:
#   "Redis here (not Memcached) because we need TTL eviction the cache layer lacks."
```
Never write a comment about what you just changed, what the code used to do, or why you're touching it. That belongs in the commit, not the file.

**3. Ceremony** (≈24%) — divider banners and trivial docstrings that echo the signature.
```python
# BAD
########################  USER SECTION  ########################
def get_user_id(self) -> str:
    """Gets the user id."""        # adds nothing over the name + type
    return self._id
# GOOD — delete the banner; delete the docstring. Document a property only if it has
# non-obvious behavior, validation, or a side effect.
```

**4. Commented-out code.** Delete it. Version control remembers; the file shouldn't carry corpses.

## What to keep — genuine comments

Keep the comment when it carries rationale the code can't:
```python
# WHY a choice was made over an alternative:
data = sorted(rows, key=by_name)   # name-sorted so the export matches the printed invoice order

# A non-obvious constraint / invariant the type can't express:
# offsets MUST stay byte-aligned — the firmware DMA faults on odd addresses

# A gotcha / warning that saves the next reader from a trap:
# do NOT await here — this runs inside the signal handler, awaiting deadlocks

# A reference that resolves a "why is this weird" question:
# workaround for upstream bug aws/aws-sdk#4521; remove when >=2.40 is pinned
```
If you can kill the comment by renaming a variable or extracting a well-named function, do that instead and drop the comment.

## Why this rule exists

What-not-why comments are the single most common AI comment failure — 40-45% of generated comments, vs ~25% for humans — because models describe observable syntax fluently but rarely infer design rationale, and RLHF rewarded "explaining." The damage is measured: codebases heavy in redundant/ceremony comments cost reviewers ~22% longer and slow onboarding ~29%; stale and contradictory comments (which over-specific AI comments become as code evolves) were traced to ~18% more bugs because developers trust the comment over the code. Worse than no comment is a confidently wrong one. Write few; make each one load-bearing.
