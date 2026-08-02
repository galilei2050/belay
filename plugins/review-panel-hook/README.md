# review-panel-hook

A panel of five read-only reviewer subagents, dispatched automatically after every
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
6. agent  →  dispatches all 5 reviewers in ONE message → they run in parallel over
             `git show HEAD`
7. agent  ·  merges the five reports, drops unsupported findings, fixes what survives
8. agent  →  commits the fixes → step 2 again, now over the new content
```

The nudge lands **at the top of the turn right after the commit**, not before it — the
roster is already in the agent's context when it reads the commit's own result, so it
cannot commit-and-move-on. Reviewing `HEAD` rather than the index is a consequence, not a
preference: `additionalContext` is delivered on the next model request, by which point the
index is empty and the commit exists.

Step 8 terminates on its own — when the reviewers return `NO FINDINGS` there is nothing to
fix, the agent makes no further commit, and the hook never fires again.

**Three ways to stay silent** (step 2), so the panel is not a tax on every Bash call: the
command is not a commit (`--dry-run`, `git status`, not a git repo); nothing is staged; or
the content was already handed to the panel. That last one is a fingerprint of the diff
under review, kept in `~/.claude/review-panel-hook/reviewed.json`, so a commit rejected by
`pre-commit` and retried does not re-dispatch five subagents — but once the agent fixes
something, the content changes and the panel runs again. `git commit -a` is read from
`git diff HEAD`, since `-a` stages at commit time and the index is still empty when the
hook fires.

It emits **no `permissionDecision`**. The commit is never blocked, never auto-approved, and
the normal permission flow (and [acl-hook](../acl-hook)) still has the last word. Advisory,
by design: the panel's job is to catch what the author missed, not to be a gate.

## The panel

One role = one class of smell, with an explicit *not your lane* section in each prompt so
findings do not arrive five times. All five are read-only (`disallowedTools: Write, Edit`)
and self-contained — the plugin works in a repo with no `rules/` directory.

| Reviewer | Catches | Grounding |
|---|---|---|
| [`duplication-reviewer`](agents/duplication-reviewer.md) | Reinvented utilities, copy-paste, parallel files, hand-rolled standards, the same guard in three places | Agentic PRs carry **1.87×** the semantic redundancy of human PRs (0.2867 vs 0.1532, p<0.001); copy/pasted lines industry-wide rose 8.3% → 15.7% while refactored lines fell 25% → <10% (GitClear, 623M changed lines) |
| [`explicitness-reviewer`](agents/explicitness-reviewer.md) | Catch-alls, sentinel defaults, unreachable `None` branches, silent fallbacks, `Any`/`any`/`type: ignore`, backends guessing at the frontend's shape | `any` added **9.0×** as often as in human PRs (2.16 vs 0.24 per PR, p≈2.3×10⁻⁷); type-bypass constructs 2.1–2.5× (d=1.45); **+47%** error-masking constructs vs pre-AI baseline (GitClear 2026) |
| [`bloat-reviewer`](agents/bloat-reviewer.md) | Long methods, deep nesting, speculative generality, dead code, padding | Long Method counts **5–6×** the human baseline in a controlled 90-problem audit; matched real-world files show 1.33× LOC, 1.47× statements/function, 1.35× nesting — while cyclomatic complexity is flat at 1.06× |
| [`solid-reviewer`](agents/solid-reviewer.md) | Mixed responsibility, wrong-layer fixes, growing if/elif chains, inverted dependencies, leaky abstractions | Across generated systems, total LOC correlates with architectural smell count at **ρ=0.94**, p<0.001; agentic refactoring is >91% trivial annotation changes, so nobody proposes the move |
| [`comments-reviewer`](agents/comments-reviewer.md) | Comments describing something other than the entity they sit on, what-not-why, narration, untrue claims | Honest caveat: comment *density* is **not** an AI-specific problem (18.01% vs 17.96%, 1.003×). What is measured is correctness — ~20% of generated comments carry demonstrable factual errors. Tests 1–2 are here by the repo owner's explicit rule, not by a published multiplier |

**Deliberately out of scope: security.** External evidence ranks it the highest-harm
category (1.57× more security findings in AI PRs; up to 2.74× for XSS), but it is a
different job from code cleanliness and Claude Code already ships `/security-review`. Do
not add a sixth seat — run that command instead.

## Install

```
/plugin install review-panel-hook@belay
```

Pairs with [`no-shirk-hook`](../no-shirk-hook): the panel produces findings, and no-shirk
stops the agent from ending the turn by asking whether to fix them.

## Config

None. The roster lives in `REVIEWERS` in `hooks/review_panel_hook.py`; adding or removing
a seat means editing the plugin — see [CLAUDE.md](CLAUDE.md).
