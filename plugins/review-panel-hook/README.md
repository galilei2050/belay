# review-panel-hook

A panel of eight read-only reviewer subagents, dispatched automatically after every
`git commit`.

## When the nudge fires

The hook runs on `PreToolUse` — *before* the Bash command executes — but the text it emits
reaches the model *after*. That split is the whole design:

```
1. agent  →  Bash: git commit -m "add feature"
2. HOOK   ·  PreToolUse fires, before git runs. Reads tool_input.command + cwd:
             · is this a command that creates a commit?      (no → silent)
             · is anything staged to review?                 (no → silent)
             · has this exact content been reviewed already? (yes → silent)
             → emits additionalContext, NO permissionDecision
3.        ·  normal permission flow runs (acl-hook still decides) → git commit executes
4.        ·  the commit lands; HEAD now points at it
5. agent  ←  tool result + the injected nudge, together, on the next model request
6. agent  →  dispatches all 8 reviewers in ONE message → they run in parallel over
             `git show HEAD`
7. agent  ·  merges the eight reports, drops unsupported findings, fixes what survives
8. agent  →  commits the fixes — findings the panel already made, so no second round
```

The nudge lands **at the top of the turn right after the commit**, not before it — the
roster is already in the agent's context when it reads the commit's own result, so it
cannot commit-and-move-on. Reviewing `HEAD` rather than the index is a consequence, not a
preference: `additionalContext` is delivered on the next model request, by which point the
index is empty and the commit exists.

**A round is owed to changes the panel has not seen, and nothing else.** The fix commit is
new content, so the hook does fire over it — the nudge itself is what tells the agent to
skip it, because only the agent knows whether what it just committed is the panel's own
findings applied or genuine new work. Eight subagents is real money, and a panel handed its
own corrections finds fresh wording to object to indefinitely. A clean panel ends it a step
earlier: `NO FINDINGS` means nothing to fix, no further commit, and no further nudge.

**Three ways to stay silent** (step 2), so the panel is not a tax on every Bash call: the
command is not a commit (`--dry-run`, `git status`, not a git repo); nothing is staged; or
the content was already handed to the panel. That last one is a fingerprint of the diff
under review, kept in `~/.claude/review-panel-hook/reviewed.json`, so a commit rejected by
`pre-commit` and retried does not re-dispatch eight subagents — but once the agent fixes
something, the content changes and the panel runs again. `git commit -a` is read from
`git diff HEAD`, since `-a` stages at commit time and the index is still empty when the
hook fires.

It emits **no `permissionDecision`**. The commit is never blocked, never auto-approved, and
the normal permission flow (and [acl-hook](../acl-hook)) still has the last word. Advisory,
by design: the panel's job is to catch what the author missed, not to be a gate.

## The panel

One role = one class of smell, with an explicit *not your lane* section in each prompt so
findings do not arrive eight times. All eight are read-only
(`disallowedTools: Write, Edit, NotebookEdit`) and self-contained — the plugin works in a repo with no
`rules/` directory.

The first four are **semantic** — is the change right, whole, and provable? The last four are
**structural** — is it written well? They are listed in dispatch order, which is also the
reading order of the merged report: a wrong answer outranks a long function.

| Reviewer | Catches | Grounding |
|---|---|---|
| [`correctness-reviewer`](agents/correctness-reviewer.md) | Algorithm and business-logic errors, inverted conditions, off-by-one and boundary mistakes, unhandled edge cases, wrong ordering, concurrency and resource-lifecycle errors | Logic & correctness is **52.6% of all findings** in AI PRs and runs **1.75×** the human rate; algorithm/business-logic **2.25×**, concurrency control **2.29×**, null dereference **2.27×** (CodeRabbit, 320 AI vs 150 human PRs). Functional bugs appear in **78%** of 72 surveyed studies |
| [`integration-reviewer`](agents/integration-reviewer.md) | Callers never updated, schemas/configs/migrations out of sync, stubs and TODOs, non-existent packages, deprecated API forms, out-of-scope edits | SWE-bench success collapses from **55–58% single-file to 11–25% multi-file**; *Incomplete Solution & Side Effects* is **29–42%** of failures (SWE-Compass); top revert causes are unintended side effects/overengineering **22.33%** and functional incorrectness 22.13%. **19.7%** of 2.23M recommended package references do not exist; **24.9–37.4%** of plausible API completions are deprecated |
| [`test-integrity-reviewer`](agents/test-integrity-reviewer.md) | **Tests written too low** — a unit test per internal function instead of one through the real boundary, mocking the layer directly beneath, assertions a pure refactor would break — plus absent or weak oracles, assertions weakened to go green, skipped cases, mocks unlike production, behavior changed with tests untouched, validation deleted | On level: the repo owner's explicit standing rule — test through the outermost boundary a real consumer uses; a unit test per internal API breaks on refactors and stays silent when the contract does. On oracles: **80.2% of 86,156 agent-authored test patches had a weak oracle or none** (33,596 PRs); only 11.3% carried one strong-oracle type. SpecBench measured **43–48pp** visible-vs-hidden test gaps, up to 100pp. The one lane where CI is structurally blind — a weakened assertion makes the build *greener* |
| [`explicitness-reviewer`](agents/explicitness-reviewer.md) | **Both directions**: guards against impossible states, catch-alls, sentinel defaults, `Any`/`type: ignore`, bare domain literals with no enum behind them (including a field merely *declared* `str`), naive datetimes and dates compared as text, backends guessing at shape — *and* real failure paths left unhandled, fatal treated as recoverable, invariants not restored, missing edge validation | `any` added **9.0×** as often as in human PRs (p≈2.3×10⁻⁷); type-bypass constructs 2.1–2.5× (d=1.45); **+47%** error-masking constructs (GitClear) — but error-handling findings **1.97×** and null-dereference **2.27×** (CodeRabbit), predominantly handling that is *missing*. Both are true; see below. Hardcoded constants trace to *indiscriminate handling of string literals* — the model does not separate a value that names a domain concept from a throwaway string (SonarQube, 5 models) |
| [`duplication-reviewer`](agents/duplication-reviewer.md) | Reinvented utilities, copy-paste, parallel files, hand-rolled standards, the same guard in three places | Agentic PRs carry **1.87×** the semantic redundancy of human PRs (0.2867 vs 0.1532, p<0.001); copy/pasted lines industry-wide rose 8.3% → 15.7% while refactored lines fell 25% → <10% (GitClear, 623M changed lines) |
| [`bloat-reviewer`](agents/bloat-reviewer.md) | Long methods, deep nesting, speculative generality, dead code, padding | Long Method counts **5–6×** the human baseline in a controlled 90-problem audit; matched real-world files show 1.33× LOC, 1.47× statements/function, 1.35× nesting — while cyclomatic complexity is flat at 1.06× |
| [`solid-reviewer`](agents/solid-reviewer.md) | Mixed responsibility, wrong-layer fixes, growing if/elif chains, inverted dependencies, leaky abstractions, free functions sharing one first argument that should be a type with methods | Across generated systems, total LOC correlates with architectural smell count at **ρ=0.94**, p<0.001; agentic refactoring is >91% trivial annotation changes, so nobody proposes the move |
| [`comments-reviewer`](agents/comments-reviewer.md) | Comments describing something other than the entity they sit on, what-not-why, narration, untrue claims — and the same in prose docs, plus a `CLAUDE.md`/`README.md` that copies code or another doc instead of linking to it | Honest caveat: comment *density* is **not** an AI-specific problem (18.01% vs 17.96%, 1.003×), and humans actually draw more spelling (1.76×) and testability (1.32×) findings. What is measured is correctness — ~20% of generated comments carry factual errors. Tests 1–2 and 4 are here by the repo owner's explicit rule, not by a published multiplier — and a copied doc is an untrue comment on a delay, read aloud to the next agent from `CLAUDE.md` as fact |

### The defensiveness paradox, and why one role owns both sides

The two headline numbers point opposite ways. GitClear measures **+47% error-masking
constructs** — catch blocks, safe navigation, null checks — which reads as "agents are too
defensive". CodeRabbit measures **1.97×** error-handling findings and **2.27×**
null-dereference findings, and theirs are predominantly handling that is *missing*.

There is no contradiction: GitClear counts constructs, CodeRabbit judges contextual adequacy.
Agents emit defensive syntax cheaply wherever it is reflexive, and still miss the
semantically required path — a 2025 Claude Code study named the mechanism *optimistic error
handling*. Over-armoured and under-armoured in the same file. A reviewer hunting only one
direction implicitly endorses the other, so `explicitness-reviewer` runs both passes and
treats "handling present, but in the wrong place" as its highest-value finding.

### Deliberately out of scope

- **Security.** Highest measured harm (1.57× overall, up to 2.74× for XSS), but a different
  job — Claude Code already ships `/security-review`. Run that.
- **Performance.** Real signal (excessive I/O **7.9×**), but static performance warnings show
  only **46% precision** against measured regressions; it needs profiling, not a diff reader.
- **Readability and naming.** Genuinely elevated (readability **3.15×**, naming 1.87×), but
  these are CodeRabbit's "fixable hurdle" findings — more common in *accepted* PRs, so they
  do not drive rejection. Not worth a ninth seat.
- **Requirements traceability and agent-trajectory safety.** The largest failure classes of
  all (requirement misinterpretation **30–34%** of failures; context amnesia in 16.3% of
  operational incidents) — but neither is visible in a commit diff. They need the original
  task and the session trajectory, which this hook does not have.

## Install

```
/plugin install review-panel-hook@belay
```

Pairs with [`no-shirk-hook`](../no-shirk-hook): the panel produces findings, and no-shirk
stops the agent from ending the turn by asking whether to fix them.

## Config

None. The roster lives in `REVIEWERS` in `hooks/review_panel_hook.py`; adding or removing
a seat means editing the plugin — see [CLAUDE.md](CLAUDE.md).
