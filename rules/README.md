# Code-smell & agent-behavior rules

Language-agnostic rules that keep AI-generated code (and AI behavior) from smelling. Cross-project: symlinked into `~/.claude/rules`, they apply to every repo on the machine, on top of — not instead of — any project-local `.claude/rules/`. Each file targets one smell family that LLM coding agents produce at a measurably higher rate than human authors.

Read the one that matches what you're about to do. They're short on purpose.

## How these load (machine-wide rollout)

Per the Claude Code docs, `.claude/rules/*.md` is auto-loaded at launch — and rules in `~/.claude/rules/` apply to **every project on the machine**. The canonical files live in this repo (`belay/rules/`, version-controlled) and are symlinked into the user-level dir:

```
~/.claude/rules → ~/Projects/belay/rules   (canonical source; edit here)
```

That's the whole rollout — every repo on this machine gets these rules, no per-repo setup. User-level rules load *before* a repo's own `.claude/rules/`, so any project can override. (Rules can't be shipped as a Claude Code *plugin* — plugin components are skills/agents/hooks/MCP/LSP/monitors only — so they live as a plain versioned dir beside belay's hook plugins.)

To keep the token cost down, the **code-specific** rules carry `paths:` frontmatter and load only when Claude touches a source file; the three pet-peeve rules and the four behavioral rules load **unconditionally** (every session). Rules are context, not enforcement — see "Rules vs. hooks" below.

## Code smells — what lands in the files

| Rule | Stops you from… |
|------|-----------------|
| ⭐ [root-cause-not-symptom.md](root-cause-not-symptom.md) | Patching the symptom (special-case, catch-all, retry, test-weakening, wrong-layer) instead of fixing the cause |
| ⭐ [no-defensive-defaults.md](no-defensive-defaults.md) | Guarding impossible states, swallowing exceptions, "just in case" optionals, silent fallbacks |
| ⭐ [comments-why-not-what.md](comments-why-not-what.md) | Comments that restate code, narrate changes, or add ceremony — instead of explaining why |
| [reuse-before-reinvent.md](reuse-before-reinvent.md) | Duplicating, reinventing, or creating a parallel file when one already exists |
| [no-speculative-generality.md](no-speculative-generality.md) | Adding parameters, abstractions, and flags for a future that hasn't arrived |
| [concrete-types.md](concrete-types.md) | Using `Any`/untyped maps at boundaries or silencing the type checker |
| [honest-tests.md](honest-tests.md) | Tests that can't fail, unfaithful mocks, or weakening the oracle to go green |
| [surface-the-smell.md](surface-the-smell.md) | Building cleanly on top of rotten code without naming or fixing it |
| [keep-it-simple.md](keep-it-simple.md) | God functions, deep nesting, long parameter lists, accreting complexity instead of simplifying |
| [no-dead-code.md](no-dead-code.md) | Leaving unused symbols, unreachable branches, commented-out code, or TODO/placeholder stubs |
| [match-the-codebase.md](match-the-codebase.md) | Writing generically-styled code that ignores the repo's existing conventions and idioms |
| [secure-by-default.md](secure-by-default.md) | Missing input validation, hardcoded secrets, injection, insecure defaults, hallucinated packages |

## Agent behavior — habits that produce bad outcomes (no single code artifact)

| Rule | Stops you from… |
|------|-----------------|
| ⭐ [finish-the-work.md](finish-the-work.md) | Stopping to ask when the next step is obvious; "done" after stage 1; claiming success without running it |
| ⭐ [ground-claims-in-data.md](ground-claims-in-data.md) | Hedging ("наверное"/"probably"/"should"), guessing, fabricating, or echoing a wrong premise instead of checking |
| [minimal-scope.md](minimal-scope.md) | Touching unasked files, ripping out working code, unreviewable whole-file rewrites |
| [be-concise.md](be-concise.md) | Wasting tokens on verbose prose, padded comments, and wall-of-text output |

⭐ = highest-priority (the user's explicit pet peeves).

## How these rules are written (and how to add one)

The format follows what actually moves a model, in priority order:

1. **Open with the imperative + the positive replacement.** Say what to do, not only what to avoid — a bare prohibition leaves the model on its default.
2. **Name the failure mode and its "tell" phrases** ("just in case", "now using X", "this should work", "наверное"). A tell the model can pattern-match beats an abstraction.
3. **Carry the load with tiny BAD/GOOD code pairs.** Concrete examples beat description.
4. **End with one "Why this rule exists" paragraph** grounded in evidence and measured rates, not vibes.
5. **One smell family per file, short** (~40-80 lines). Long files get partially ignored.

Cross-link related rules by filename so the agent can follow the thread.

## Rules vs. hooks

A prompt rule shapes behavior but fails silently a meaningful fraction of the time. For a smell that's **cheaply detectable from the diff** and **high-cost** (bare `except Exception`, `# type: ignore`, a weakened assertion, a hardcoded secret, a hedge word in a claim), back the rule with a deterministic `PreToolUse`/review hook that inspects the content and blocks — enforcement the model can't be talked out of. Keep prompt rules for judgment calls; use hooks for the crisp mechanical ones.

## Provenance

Built from a hypothesis-tree investigation (methodology: `docs/metric-investigation-methodology.md`). Evidence: the 16-hypothesis catalog `docs/agent-code-smells-2026-05.md`, the findings summary `docs/agent-code-smell-rules-findings-2026-06.md`, and a four-source Perplexity sweep (academic / security / Reddit-HN / rule-collections). Investigation notes: `.work/code-smell-rules/`.
