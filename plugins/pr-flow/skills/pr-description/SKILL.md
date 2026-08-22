---
name: pr-description
description: Writes or refreshes the description of a pull request — the body a reviewer reads before the diff. Use when opening a PR (`gh pr create`), when a push has made an existing PR's body stale, or when the user asks to write up / re-write / оформить a PR. Produces the failure with its measured numbers, a mermaid diagram of the mechanism, what changed, what was verified, the risk and the rollback. NOT for commit messages (one commit, one why) and not for design docs.
---

# PR description

**A PR description exists to answer one question: what does the reviewer need to know that
the diff will not tell them?** The diff already says which lines changed. It cannot say what
was broken, how it broke, how often, what you decided not to do, or what you checked. That is
the whole content of the body.

Rewriting the diff in prose ("adds `existing_order.py`, updates `create_estimate` to…") is the
default failure. It costs the reviewer a screen and tells them nothing.

## Step 1 — collect the facts before writing a word

Never write a number you did not measure. Run these first (adapt to the repo):

```bash
gh pr view --json number,url,state,body        # is there already a PR? what does it claim?
git log --oneline main..HEAD                   # what work is on this branch
git diff main...HEAD --stat                    # size and shape
```

Then gather what the diff cannot supply, from where it actually lives:

- **The failure this fixes** — the trace, the log line, the production count, the ticket. Real
  IDs and real counts ("62 orders created, 23 archived by staff") beat "several duplicates".
- **What you verified** — the exact test/lint/CI command and its actual result. If a check did
  not run, it did not run: say so, do not imply it passed.
- **What stayed out of scope** — a decision agreed but not implemented, a follow-up, a
  blocked check. This is the highest-value part of the body and the first thing agents drop.

If you cannot state the cause in one sentence, you have not found it yet — read more before
writing the PR. (`rules/root-cause-not-symptom.md`)

## Step 2 — the shape

Sections in this order. Drop any that has nothing real in it; never pad one to fill it.

| Section | Holds | Skip when |
|---|---|---|
| **What was wrong** | the failure, its mechanism, its measured size | the PR adds something new that never broke |
| **What changed** | the decision and where the invariant now lives — not a file list | never |
| **What the review found** | defects a review round caught that tests did not | no review round ran |
| **Checks** | the commands you ran, each with its real outcome | never |
| **Risk / rollback** | blast radius, feature flag, how to undo | a docs-only change |
| **Open ends** | agreed-but-unimplemented, follow-ups, blocked checks | genuinely none |

A title is a claim about the outcome, not about the diff: `Stop the agent creating duplicate
orders`, not `Update service_orders and add guard`.

## Step 3 — diagram what prose explains badly

Draw only once the facts from Step 1 are collected — a diagram invented before the numbers is
a diagram that will contradict them.

**`references/diagrams.md` picks the form, sets how many, and shows the syntax.** Read it
before drawing.

## Step 4 — check the body against these before posting

- Every number traces to something you ran or read. No "probably", no "should", no invented
  percentage. (`rules/ground-claims-in-data.md`)
- Nothing is a translation of the diff into English.
- A decision that was made but *not* implemented in this PR says so in bold, with a pointer to
  where it is written down.
- Checks list real outcomes, including the ones that failed or were skipped and why.
- No commit-by-commit narration, no "as requested", no changelog of your own process.
- Language of the body matches how the repo and the user write. Do not translate a
  Russian-speaking team's PR into English because English is the default.

## Step 5 — post it

New PR — write the body to a file, never inline in the shell (the ```` ``` ```` fences are
backticks, and a double-quoted shell argument runs them as command substitution):

```bash
gh pr create --title "<outcome claim>" --body-file <file>
```

Existing PR after a push — read the current body, then update it rather than appending:

```bash
gh pr view --json body -q .body > /tmp/pr-body.md   # read what it claims now
gh pr edit <number> --body-file <file>
```

What to update after a push: the **Checks** section (new results), **What changed** if the
push changed the decision, **Open ends** if the push closed one. A body that describes the
first commit of a five-commit branch misleads the reviewer more than an empty one.

`references/worked-example.md` is a real PR body with the diagrams that made a reviewer change
the decision — read it if the shape above is not concrete enough.
