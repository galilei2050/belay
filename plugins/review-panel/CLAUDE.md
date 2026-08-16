# CLAUDE.md — review-panel

How to change the hook and the panel without breaking either.

## Scope (don't expand it)

The hook decides **one** thing: is this Bash call a commit the panel has not seen and big
enough to be worth a round? It does NOT review code, call an LLM, read the transcript, or
know anything about the reviewers beyond their names. All the judgment lives in
`agents/*.md`, which are prompts, not code.

It touches the diff twice, and both are blind to what the diff *says*: a hash of it, and a
count of its changed lines. If a change would make the hook read the diff for meaning —
which files, which language, whether a change looks risky — it belongs in a reviewer prompt
instead.

## Advisory is the design, not a weaker version of a gate

The hook emits `additionalContext` and **never** a `permissionDecision`. Two reasons, and
both matter:

1. `permissionDecision: "allow"` bypasses the permission system entirely — it would
   override acl-hook and the user's own settings for every commit. A reviewer panel has no
   business handing out that authority.
2. `deny` would make the commit itself the gate. That was considered and rejected: it puts
   eight subagents between the agent and every `git commit`, including trivial ones, and it
   inverts belay's own rule that reversible local work is never a reason to stop.

Don't "upgrade" this to a blocking gate. If a blocking review gate is wanted, it is a
different plugin with a verification artifact (see `docs/PHILOSOPHY.md`), not a flag here.

## Why the digest exists

Without it, a commit rejected by `pre-commit` and retried re-dispatches eight subagents over
byte-identical content. The digest is over the code under review, not the command, so:

- retry of the same content → silent
- agent fixed something and re-committed → new content, panel runs again
- `git commit -a` → the digest comes from `git diff HEAD`, since `-a` stages at commit
  time and the index is still empty when the hook fires

Keep the state file dumb (repo path → digest). It is a de-duplicator, not a review log; if
you ever need history, that is a log under `~/.claude/logs/`, not this file.

## A round is owed to unseen changes, and that rule lives in the prompt

The panel is for work the panel has not read. The digest already stops a byte-identical
retry, but it cannot stop the commit that *applies the findings* — that is new content by
every mechanical measure, and telling it apart from genuine new work needs to know what the
commit was for. Only the agent knows that, so the rule is in `_PROMPT` and the hook keeps
guessing nothing (see Scope).

## The size threshold, and the cost it accepts

`MIN_CHANGED_LINES = 64` is the second bound on the spend, and it is the one the hook *can*
compute: `git commit` is the exact moment the size of a change is known for free. Eight
subagents over a two-line diff is not a trade anyone makes on purpose.

It buys that with a known loss — a flipped condition committed on its own now goes
unreviewed. That was weighed and accepted (the panel is advisory; a small commit whose
correctness is load-bearing can be handed to the reviewers by hand). Don't reintroduce the
small-commit case by lowering the number to near-zero; move it deliberately or not at all.

Count added *and* removed lines, minus the `+++`/`--- ` file headers — a rewrite that
replaces 40 lines with 40 is an 80-line review, not a zero-line one.

One hook-side "fix" that looks tempting and is not: going quiet after every dispatch. It
silences the next commit whenever the panel came back clean, and a skipped review leaves no
trace at all.

## Changing the panel

**The ceiling is eight seats — four semantic, four structural.** Every added reviewer costs
a subagent run on every commit, and overlapping roles produce the same finding eight times,
which trains the agent to skim the report. Adding a ninth means arguing one of the eight out.

The ceiling was five, and it was raised deliberately in 0.2.0. The original five were all
structural — duplication, defensiveness, bloat, SOLID, comments — and a second research pass
showed that the panel therefore had no seat for the classes that actually cost the most:
logic/correctness is **52.6% of all findings** in real AI PRs, **80.2%** of agent-authored
test patches carry a weak or absent oracle, and multi-file success collapses from 55–58% to
11–25%. Those three became `correctness-reviewer`, `test-integrity-reviewer`, and
`integration-reviewer`. Keep that balance: **if a new seat is structural, it almost certainly
duplicates an existing one.**

Four classes were considered for a seat and rejected — do not re-litigate them without new
evidence. Security (`/security-review` already ships), performance (static warnings show 46%
precision; it needs profiling), readability/naming (elevated, but these are the findings that
appear *more* in accepted PRs, so they do not drive rejection), and requirements traceability
plus agent-trajectory safety (the largest failure classes of all, but neither is visible in a
commit diff — they need the original task and the session trajectory, which this hook does
not have).

To add or change a role:

1. **Justify it with measured evidence** — an AI-vs-human rate from a real study, not a
   plausible-sounding smell. The README table carries each seat's number; a new seat needs
   its own row. "This seems bad" is not a seat.
2. **Give it an exclusive lane.** Write the *Not your lane* section first, naming which
   existing reviewer takes each adjacent smell. If you cannot draw that line, the role is a
   duplicate of one that already exists.
3. **Keep the prompt self-contained.** Reviewers must work in a repo with no `rules/`
   directory. Yes, that duplicates wording with `rules/*.md` — that trade was made
   deliberately so the plugin installs anywhere. Don't "fix" it by reaching out to `rules/`.
4. **Keep it read-only.** `disallowedTools: Write, Edit, NotebookEdit`. A reviewer that
   edits is no longer a reviewer, and the main agent loses the merge-and-decide step.
5. Add the name to `REVIEWERS` in the hook, add the README row, bump the plugin version.

The lanes that are easiest to blur. State the boundary explicitly in any new prompt:

- **`bloat-reviewer` judges size, `solid-reviewer` judges placement.** A 200-line function is
  bloat; a function doing two jobs that belong in different modules is SOLID.
- **`correctness-reviewer` judges the answer, `explicitness-reviewer` judges the failure
  mode.** A wrong number is correctness; a swallowed exception or an unhandled failure path
  is explicitness. Correctness may only take a guard when it can name the input that produces
  a wrong result.
- **`correctness-reviewer` judges whether it is wrong, `test-integrity-reviewer` judges
  whether a test would notice.** Both can fire on one function; that is not duplication.
- **`test-integrity-reviewer` owns everything about tests** — both whether they can fail and
  whether they sit at the right level. "Too low-level" is about what a test *reaches for*,
  never about how long it is; test verbosity belongs to nobody, and `bloat-reviewer`'s
  standards must not leak into test files.
- **`solid-reviewer` judges where a thing should live, `integration-reviewer` judges what
  breaks because it moved.**
- **A bare domain literal belongs to `explicitness-reviewer`, not `duplication-reviewer`.**
  The defect is the undeclared value set, not the repetition — so it is a finding on the
  first occurrence, where a DRY reviewer only ever sees a repeated *block* and never the
  value on its own. Duplication keeps the block.
- **A naive datetime is `explicitness-reviewer`'s on sight; a DST-boundary wrong answer is
  `correctness-reviewer`'s.** Same split as every other implicit contract: explicitness owns
  "this value does not declare whose clock it is", correctness owns "this input produces the
  wrong number". Correctness still needs its input→output pair; explicitness does not.
- **A type that two free functions were already hand-rolling is `solid-reviewer`'s; a type
  nobody needed is `bloat-reviewer`'s speculative generality.** The threshold is two places
  sharing a subject. Below it the reviewers would contradict each other, which is why the
  number is written into both prompts.
- **`comments-reviewer` owns prose, including whole doc files.** A `CLAUDE.md` that pastes
  code instead of linking is a copy written in English — its harm is staleness and a reader
  trusting it, which is that role's whole subject. `duplication-reviewer` keeps copies of
  the *code*, and `integration-reviewer` keeps the doc that broke because the code moved.

`explicitness-reviewer` deliberately owns **both** directions — over-armoured and
under-armoured. Don't split it: the evidence says agents do both in the same file (GitClear
counts +47% error-masking constructs, CodeRabbit finds 1.97× *missing* handling), and a
reviewer hunting one direction implicitly endorses the other. Its highest-value finding is
"handling present, but in the wrong place."

## What every reviewer prompt must end with

`NO FINDINGS` on a clean diff, verbatim and alone. The main agent merges eight reports; a
reviewer that pads a clean result with observations makes the merge unreadable and teaches
the agent to ignore the panel.

## Testing

```
uv run pytest plugins/review-panel/ -q
uv run ruff check plugins/review-panel/
uv run mypy plugins/review-panel/hooks/review_panel_hook.py
```

**Test at the highest level, never the internals.** Every test drives the hook through the
boundary Claude Code uses — the real script, a JSON payload on stdin, JSON on stdout — via
the `run_hook` fixture. Nothing imports `review_panel_hook` or calls its functions.

That is deliberate, not incidental. `is_reviewable_commit()`, `review_scope_digest()`, and
the state helpers are implementation; asserting on them directly would freeze the internals
and still tell you nothing about what the agent receives. Going through the script also
covers what a unit test cannot: that the file is executable, imports cleanly, resolves `git`,
exits 0, and writes parseable JSON. When you add behavior, add a test that says **what the
agent would see**, and let the internals stay refactorable.

Two supporting fixtures make that possible:

- `repo` builds a real throwaway git repo — the hook's whole job is reading git state, and a
  mocked git would test nothing.
- `run_hook` points `HOME` at `tmp_path`, so the dedupe state the hook writes never touches
  the real `~/.claude` and each test starts with the panel having seen nothing.

The only tests that read files directly are the prompt-contract ones, and they assert on the
*shipped artifacts* (frontmatter name, read-only tools, the `NO FINDINGS` clause) rather than
on any function.

Before trusting a new test, break the thing it covers and watch it fail. The roster test was
verified that way: adding a name to `REVIEWERS` with no matching `agents/<name>.md` makes it
red, which is exactly the bug it exists to catch.
