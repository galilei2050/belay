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

## Why the extension list stays narrow

The *why* is in the `UI_SUFFIXES` comment in the hook. The policy is here: the tempting fix
for the excluded ecosystems is to sniff the content for `Widget`, `@Composable` or
`View {`. Don't — it makes the hook understand the diff, which is the one thing Scope
forbids. If Flutter, SwiftUI or Compose need coverage, the honest answer is that the skill
still triggers on its own description and `/ui-review` still works.

## The known gap in the commit-time dedupe

`claim_review` runs at `PreToolUse`, i.e. **before** the commit exists. If that `git commit`
then fails for a reason the content does not change — a rejected message, a signing failure
— the agent has already been handed a roster pointing at the previous `HEAD`, and the
identical retry that actually lands is silenced as a duplicate.

Not fixed here, deliberately: the correct key is the resulting commit sha, which a
`PreToolUse` hook cannot know, and `review-panel` makes the same trade with the same
digest-of-staged-content design. Changing it in one of the two plugins and not the other
buys a divergence worth more than the case it fixes. If it is ever fixed, fix both — the
shape would be a `PostToolUse` claim, not a smarter digest.

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

`make ci` is the gate, as everywhere in this repo. `uv run pytest plugins/usable-ui/ -q`
narrows it while iterating.

**Test at the highest level, never the internals.** Every test that exercises the hook
drives it through the boundary Claude Code uses — the real script, JSON on stdin, JSON on
stdout — via the `hook` fixture. Nothing imports `usable_ui_hook`. The rest read shipped
artifacts (the agent prompts, `hooks.json`, the command file), because those are what the
loader reads and the hook never computes them.

The fixtures worth knowing about: `clean_git_env` is autouse and drops inherited `GIT_*`
vars, without which the fixture repo's commits land in belay itself when the suite runs
under `pre-push`; `repo` builds a real throwaway git repo, because the hook's job is reading
git state and a mocked git would test nothing; `hook` points `HOME` at `tmp_path / "home"`
and holds it for the whole test, which is what lets one test assert across two calls — that
is how the once-per-session and retry-dedupe tests work.

Two invariants the tests pin because nothing else can: the roster in the hook, the roster in
`commands/ui-review.md`, and the shipped `agents/*.md` must name the same five; and the
tools under test must be the tools `hooks.json` actually matches, so a matcher can never
list a tool the hook ignores.

Before trusting a new test, break the thing it covers and watch it fail. The roster test was
verified that way: adding a name to `REVIEWERS` with no matching `agents/<name>.md` makes it
red, which is exactly the bug it exists to catch.
