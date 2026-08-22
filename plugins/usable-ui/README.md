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
2025) generated 80 banking UIs with GPT-4-turbo and Claude 3.5 Haiku and compared
accessibility-agnostic against accessibility-oriented prompting:

| Measure | Without rules | With rules |
|---|---:|---:|
| Expert-judged violation rate | 58% | **19%** |
| Mean severity (0–4) | 1.53 | **0.30** |
| Adequate touch targets | 32% | **100%** |
| Visible focus indicators | 56% | **98%** |
| Keyboard operability | 48% | **94%** |
| ARIA labels | 28% | **95%** |

Aljedaani et al. (2024) found **84%** of ChatGPT-generated websites carried accessibility
violations with no such prompt; A11yn (2025) cut a code model's inaccessibility rate from
0.38 to 0.15.

The same study is why the reviewers are subagents and not a linter: the *automated*
violation rate barely moved between the two conditions (15.93% → 17.32%) while the expert
rate collapsed. Axe sees a missing accessible name. It does not see a name that is wrong, a
focus order that makes no sense, or an empty state that a failed request also renders.

## The skill

`usable-ui:ui-decisions` classifies the element first — **action, destination, object,
setting, or event** — and lets the class pick everything downstream:

| Class | Label grammar | Control |
|---|---|---|
| Action | imperative verb + object — `Send SMS` | `<button>` |
| Destination | noun — `Billing` | `<a href>` |
| Object / view | noun phrase — `Payment methods` | heading, tab |
| Setting (immediate) | what it controls — `Email notifications` | switch |
| Setting (deferred) | what it controls | checkbox |
| Event | object + past tense — `SMS sent` | list row |

Then: the label (verb-first, never `OK`/`Yes`/`Submit` on a decision, natural word order,
sentence case, 1–3 words), the control (button vs link, switch vs checkbox, radios vs
select at 2–7 vs 8+, modal vs inline vs page), the placement (dialog order by platform,
form submit by the form rule, dividers only where whitespace cannot carry the boundary),
the five states every screen owes, and the accessibility floor (24×24 CSS px / 44 pt /
48 dp, 4.5:1 and 3:1, names, focus, never colour alone).

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

- **`Write`/`Edit`/`MultiEdit`/`NotebookEdit`** on a UI file → the rules, **once per
  session**. Repeating them on every component teaches the agent to skim past them.
- **`git commit`** whose staged diff touches a UI file → the roster of five, deduplicated by
  a digest of that UI content, so a commit rejected by `pre-commit` and retried does not
  re-dispatch the panel.

It is **advisory**: it emits only `additionalContext`, never a `permissionDecision`, so the
call still goes through the normal permission flow and `acl-hook` keeps the last word.

Trigger extensions: `.jsx .tsx .vue .svelte .astro .html .htm .hbs .ejs .pug .erb .haml
.liquid .twig .njk .mustache .jinja .jinja2 .j2 .blade.php .razor .cshtml .xaml .axaml
.qml`. Deliberately narrow — `.swift`, `.dart`, `.kt` and stylesheets stay out, because
telling a widget from a repository means reading the file, which is the reviewers' job and
not the hook's. In those projects the skill still triggers on its own description, and
`/ui-review` still works.

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
