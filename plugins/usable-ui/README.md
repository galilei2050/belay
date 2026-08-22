# usable-ui

Makes the agent's interfaces understandable: what an element is called, which control it
should be, where it goes, and which states it owes. Not visual design — nothing here picks
a colour or a font.

Three parts, one concern:

- **`ui-decisions`** — a skill carrying the decision rules, with five reference files for
  depth (naming, controls, layout, states, accessibility).
- **Five read-only reviewers** — `ui-copy`, `ui-control`, `ui-layout`, `ui-state`,
  `ui-a11y`, each with an exclusive lane.
- **A `PreToolUse` hook** — hands the rules over the first time a session edits a
  user-facing file, and hands over the reviewer roster when a commit touches one.

## Why this exists

Rules in the model's context is a measured intervention, not a hope. Mowar et al. (W4A
2025) generated 80 banking UIs with GPT-4-turbo and Claude 3.5 Haiku: expert-judged
violations fell from **58% to 19%** and adequate touch targets rose from **32% to 100%**
when the prompt carried the rules. Aljedaani et al. (2024) found **84%** of
ChatGPT-generated websites carried accessibility violations with no such prompt. The
per-criterion figures are in `skills/ui-decisions/SKILL.md`.

The same study is why the reviewers are subagents and not a linter: the *automated*
violation rate barely moved between the two conditions (15.93% → 17.32%) while the expert
rate collapsed. Axe sees a missing accessible name. It does not see a name that is wrong, a
focus order that makes no sense, or an empty state that a failed request also renders.

## The skill

`usable-ui:ui-decisions` classifies the element first — **action, destination, object,
setting, or event** — and lets the class pick everything downstream: the grammar of the
label (`Send SMS` vs `Outbound SMS` vs `SMS sent`), the control, the placement, and the
states. The class table and the rules are in `skills/ui-decisions/SKILL.md`; the depth,
including every threshold, is in `skills/ui-decisions/references/`.

Where design systems genuinely disagree — dialog button order, sentence vs title case,
on-blur vs on-submit validation — the reference names the disagreement and tells the agent
to follow the host platform rather than average the two.

Sources: WCAG 2.2, Apple HIG, Material 3, Microsoft, GOV.UK Design System, Carbon,
Shopify Polaris, Atlassian, NN/g.

## The reviewers

| Reviewer | Takes |
|---|---|
| `ui-copy-reviewer` | grammar against the element's class, `OK`/`Yes`/`Submit` on decisions, `SMS outbound` word order, a category where a name is known, terminology drift, error and empty-state wording |
| `ui-control-reviewer` | `<div onClick>`, links that mutate and buttons that navigate, a switch that waits for Save, radios vs select vs combobox at the wrong length, modal misuse, two primaries, a submit disabled for an incomplete form |
| `ui-layout-reviewer` | dialog button order against the host platform, the form rule crossed with the dialog rule, destructive beside safe, a divider between every row — and its mirror, a timeline with no boundary between events, label placement, required/optional convention, DOM order vs visual order |
| `ui-state-reviewer` | missing loading/empty/error/success renderings, an empty state a failed request also produces, destructive actions with neither undo nor confirmation, validation on every keystroke, a form that discards input on error, duplicate submits |
| `ui-a11y-reviewer` | the pass/fail floor, each finding cited to its success criterion — names, label-in-name, labels vs placeholders, target size, contrast, colour-alone, keyboard, focus order and visibility, dialog focus handling |

The seams overlap on purpose in exactly one place: a `<div onClick>` is the wrong control
*and* an inaccessible one. Each prompt's **Not your lane** section says who takes what, and
the merge step says to report it once.

A clean reviewer replies exactly `NO FINDINGS`.

## The hook

One predicate — *is this file a user-facing surface?* — applied at two moments:

- **`Write`/`Edit`/`MultiEdit`** on a UI file → the rules, **once per session**. Repeating
  them on every component teaches the agent to skim past them.
- **`git commit`** whose staged diff touches a UI file → the roster of five, deduplicated by
  a digest of that UI content, so a commit rejected by `pre-commit` and retried does not
  re-dispatch the panel.

It is **advisory**: it emits only `additionalContext`, never a `permissionDecision`, so the
call still goes through the normal permission flow and `acl-hook` keeps the last word.

Trigger extensions: the `UI_SUFFIXES` list in `hooks/usable_ui_hook.py` — component and
template files only. Deliberately narrow: `.swift`, `.dart`, `.kt` and stylesheets stay
out, because telling a widget from a repository means reading the file, which is the
reviewers' job and not the hook's. In those projects the skill still triggers on its own
description, and `/ui-review` still works.

## Manual entry point

```
/ui-review              # the panel over `git diff HEAD`, no commit needed
/ui-review src/Cart.tsx # or a path you name
```

## Install

```
/plugin marketplace add galilei2050/belay
/plugin install usable-ui@belay
```

## Test

```
uv run pytest plugins/usable-ui/ -q
```
