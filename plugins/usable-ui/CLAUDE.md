# CLAUDE.md — usable-ui

How to change the skill, the panel and the hook without breaking any of them.

## Scope (don't expand it)

The hook decides **one** thing: is this file a user-facing surface? It does not read
markup, does not judge a label, does not know a `<button>` from a `<div>`, and knows the
reviewers only by name. Every piece of judgment lives in `skills/` and `agents/`, which are
prompts rather than code.

If a change would make the hook *understand* the UI, it belongs in a reviewer prompt.

## Advisory is the design

`additionalContext` only, never a `permissionDecision`. Same reasoning as `review-panel`:
`allow` would bypass the permission system and override acl-hook for every commit, and
`deny` would put five subagents between the agent and every `git commit`. Don't "upgrade"
this to a gate.

## Why the extension list is narrow, and why it stays that way

`.swift`, `.dart`, `.kt` and stylesheets are excluded on purpose. SwiftUI, Flutter and
Compose are genuinely UI, but those extensions also carry repositories, models and
networking, and telling them apart requires reading the file — which is the reviewers' job.
A false positive costs five subagents of the user's money on every commit.

The tempting fix is to sniff the content for `Widget`, `@Composable` or `View {`. Don't: it
makes the hook understand the diff, which is the one thing Scope forbids. If those
ecosystems need coverage, the honest answer is that the skill still triggers on its own
description and `/ui-review` still works.

Stylesheets are excluded for a different reason: a contrast or target-size change that
matters travels in the same commit as the component it styles, and that component is on the
list. A pure-CSS commit would dispatch five reviewers so that one of them has work.

## The lanes, and where they are easiest to blur

Five seats, each with an exclusive lane. Every prompt's **Not your lane** section is part of
the contract — write it first when changing a role.

- **`ui-copy-reviewer` owns the wording, `ui-control-reviewer` owns the widget.** A button
  labelled `Invoice` is copy; a `<div>` behaving as a button is control.
- **`ui-copy-reviewer` owns the wording of an `aria-label`, `ui-a11y-reviewer` owns its
  existence.** `Trash icon` is copy's finding; no label at all is a11y's.
- **`ui-state-reviewer` owns whether the state exists, `ui-copy-reviewer` owns what it
  says.** A missing error branch is state; `Something went wrong` is copy.
- **`ui-control-reviewer` owns the choice of surface (toast vs banner vs modal),
  `ui-layout-reviewer` owns where it lands.**
- **`ui-layout-reviewer` owns placement, `ui-a11y-reviewer` owns the criterion.** A
  colour-only distinction between two adjacent actions is a placement finding *and* a
  SC 1.4.1 finding; each reports its own side, and the merge step collapses them.
- **A `<div onClick>` is the one deliberate double-hit** — wrong control plus missing name
  and keyboard path. Both prompts say to report their own side; the roster prompt tells the
  agent to merge it into one finding with two reasons.

## Adding or changing a reviewer

1. **Give it an exclusive lane, written first.** If you cannot name which existing reviewer
   takes each adjacent smell, the seat is a duplicate.
2. **Cite what backs it.** Design-system guidance and WCAG criteria for the rules; measured
   rates only where a study actually measured them. Several UI defect classes have **no
   published rate** — generic button labels, `div`-instead-of-`button`, dialog defects,
   missing empty states. Those prompts say so explicitly. Do not invent a multiplier to make
   a section look stronger.
3. **Keep it read-only** (`disallowedTools: Write, Edit, NotebookEdit`) and keep it
   self-contained — it must work in a repo with no `rules/` directory.
4. **End with `NO FINDINGS`** on a clean diff, verbatim and alone.
5. Add the name to `REVIEWERS` in the hook, to `commands/ui-review.md`, to the README table,
   and bump the plugin version.

Five is a ceiling, not a target. The seat that keeps being proposed and does not exist is
"visual design" — colour, typography, spacing scale. It is out of scope by design: it is
taste with a brand attached, no cited system agrees on it, and a reviewer that flags it
trains the agent to ignore the four that carry criteria.

## Where the rules live, and the one duplication that is deliberate

The skill (`skills/ui-decisions/`) is for the agent *writing* UI; the agents are for the
agent *reviewing* it. They restate the same rules, and that is intentional — a subagent gets
a fresh context and cannot rely on the skill having been loaded in the parent. Keep them in
sync by hand when a rule changes; don't try to make one import the other.

The skill's `references/*.md` carry the depth and the named disagreements between design
systems. `SKILL.md` stays short enough to be read in full — if it grows past roughly 150
lines, the new material belongs in a reference.

## Testing

```
uv run pytest plugins/usable-ui/ -q
uv run ruff check plugins/usable-ui/
uv run mypy plugins/usable-ui/hooks/usable_ui_hook.py
```

**Test at the highest level, never the internals.** Every test drives the hook through the
boundary Claude Code uses — the real script, JSON on stdin, JSON on stdout — via the `hook`
fixture. Nothing imports `usable_ui_hook`.

Two fixtures make that possible: `repo` builds a real throwaway git repo (the hook's job is
reading git state, so a mocked git would test nothing), and `hook` points `HOME` at
`tmp_path` so the dedupe state never touches the real `~/.claude` and each test starts fresh.
The `hook` fixture holds that `HOME` for the whole test, which is what lets one test assert
across two calls — that is how the once-per-session and retry-dedupe tests work.

Before trusting a new test, break the thing it covers and watch it fail. The roster test was
verified that way: adding a name to `REVIEWERS` with no matching `agents/<name>.md` makes it
red, which is exactly the bug it exists to catch.
