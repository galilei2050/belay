---
name: ui-decisions
description: Decides the wording, the control, the placement, and the states of a user-facing interface element — what a button is called and whether it is a verb or a noun, button vs link vs toggle vs checkbox, modal vs inline, dialog button order, what a list row says, which states a screen owes, and the accessibility floor it must clear. Use when writing or changing anything a user reads or clicks: components, screens, forms, dialogs, menus, tables, timelines, empty/error/loading states, notifications, CLI/TUI prompts. NOT for visual design — colour, typography, spacing scale, brand.
---

# UI decisions

**Classify the element first. The class decides the word, the control, the place, and the
states — in that order.** Every rule below is mechanical: it takes an element and returns an
answer, not a taste.

This is not visual design. Nothing here picks a colour or a font.

## Step 1 — classify, before writing a single string

Six classes. Read what the element *does*, not what it looks like.

| Class | It… | Label grammar | Control |
|---|---|---|---|
| **Action** | performs or commits something | imperative verb (+ object): `Send SMS` | `<button>` |
| **Destination** | navigates somewhere | noun: `Billing` | `<a href>` |
| **Object / view / section** | names a thing or a body of content | noun phrase: `Payment methods` | heading, tab, card title |
| **Setting — immediate** | changes the running system on flip | noun phrase of *what it controls*: `Email notifications` | switch |
| **Setting — deferred** | is a value submitted later with a form | noun phrase | checkbox / radio / select |
| **Event / status** | reports what already happened | object + past-tense verb: `SMS sent` | list row — not a control |

Misclassification is the most expensive error on this page: it produces a `<div>` that
looks like a button, a switch inside a form with a Save button, or a timeline row worded
like a command. Get the class right and most of the rest falls out.

```
BAD  — a timeline row labelled `Send SMS` (that is a command; the SMS was already sent)
GOOD — `SMS sent · 14:02` — event class, past tense, actor named if known
BAD  — a row that says `Human` where a person answered
GOOD — the person's name: `Sergei Kulp` — an entity, so name the entity, not its category
```

## Step 2 — write the label

1. **Action → verb first.** `Create invoice`, `Remove member`. One-word commands only when
   the object is unmistakable (`Save`, `Cancel`, `Delete`).
2. **Never `OK` / `Yes` / `No` / `Submit` / `Confirm` on a decision.** The button restates
   the action: title `Delete account?` → buttons `Delete account` / `Keep account`. `Cancel`
   survives only when it genuinely abandons without committing. (Apple permits `OK` on a
   purely informational alert — that is the one exception.)
3. **Natural word order; front-load the differentiator.** `Outbound SMS`, never
   `SMS outbound`. If every row is an SMS, `Outbound` is a direction column, not a prefix.
4. **Length:** buttons and form labels 1–3 words, tabs and alert actions 1–2. A longer
   unambiguous label beats a short vague one.
5. **Sentence case** everywhere — except native Apple menus, alert buttons and tabs, which
   use title case.
6. **Same action, same word, everywhere.** `Sign in` in the nav and `Log in` on the button
   is a defect.
7. **No vague verbs as navigation** — `Explore`, `Discover`, `Learn` carry no information
   scent. Name the destination.

Depth, including menus, tabs, headings, timeline entries and icon-only controls:
`references/naming.md`.

## Step 3 — pick the control

- **Mutates / submits / opens a dialog → `<button>`. Navigates → `<a href>`.** Styling does
  not change this. A clickable `<div>` is always wrong.
- **Binary setting that takes effect immediately → switch.** Binary value submitted later
  with a form → **checkbox**. A switch next to a `Save` button is a contradiction.
- **One choice from 2–7 visible, comparable options → radio group.** 8+, or space is tight
  → select. Very long, data-loaded, or free-text allowed → combobox. 2–5 short peers that
  swap a view → segmented control.
- **Modal only for a short focused task (~1–2 fields) or a decision that must block.** More
  than four fields, or scrolling inside → page, sheet, or side panel. Never a modal just to
  announce success.
- **Tabs** for peer views one-at-a-time; **accordion** when several sections may be open or
  vertical space is tight; plain headings when most users need all of it.
- **One primary action per task area.** Destructive styling only for genuinely destructive
  consequences — never on `Cancel` or `Back`.
- **Do not disable a submit button because the form is incomplete.** Let it be pressed and
  explain what is missing. Disable only for permission, dependency, or an in-flight request.

Depth and the systems' disagreements: `references/controls.md`.

## Step 4 — place it

- **Dialog buttons follow the host platform.** Apple/Material: cancel leading, confirm
  trailing. Windows: `OK`, then `Cancel`. Mirror for RTL. Do not invent a third order.
- **Form submit goes after the last field, aligned to the form's leading edge** (left in
  LTR) — this is *not* the dialog rule, don't cross them.
- **Separate a destructive action from the safe one it sits beside** — space plus a second
  distinguishing signal, not just red.
- **Group by whitespace first; add a divider only when whitespace cannot carry the
  boundary** — between unrelated sections, or between events in a dense timeline. A line
  between every row is decoration, not structure.
- **Field labels above the input, always visible, programmatically bound.** Placeholders
  are examples, never labels.
- **Mark the minority:** mostly-required form → mark `(optional)`; mostly-optional → mark
  `(required)`. Pick one convention per product; never colour alone.

Depth: `references/layout.md`.

## Step 5 — owe every screen its states

A screen is not done when the happy path renders. Five states, each one asked explicitly:

1. **Loading** — ≤0.1 s nothing; ~1 s keep flow; 1–10 s continuous feedback; >10 s
   determinate progress plus a way to cancel or background it. Skeleton for initial load of
   a known layout, spinner for an isolated indeterminate action.
2. **Empty** — and *which* empty: first use, genuinely none, no results, filtered out, no
   permission. Each gets different copy and a different next action. Never a bare `No data`.
3. **Error** — what happened → why, if useful → what to do next. Keep what the user typed.
   Never a bare code, never blame.
4. **Partial / stale** — some of it failed or is out of date, and the user can tell.
5. **Success** — the result is visible, or it is confirmed where the user is looking.

Destructive actions: **prefer doing it plus a durable `Undo`** when reversal is reliable.
Confirm instead when it is irreversible, bulk, security-sensitive, or costly — and for the
truly dangerous, require typing the resource name.

Depth, including validation timing and toast vs banner vs modal: `references/states.md`.

## Step 6 — the floor, which is not negotiable

Numbers, from WCAG 2.2 / Apple HIG / Material 3. These are pass/fail, not preferences:

- Pointer targets **≥ 24×24 CSS px** (WCAG 2.2 AA, SC 2.5.8); prefer 44×44. Native touch:
  **44×44 pt** (Apple), **48×48 dp** (Material).
- Contrast **4.5:1** normal text, **3:1** large text (SC 1.4.3) and **3:1** for control
  boundaries, icons and state indicators (SC 1.4.11).
- Every control has a non-empty accessible name; an icon-only button needs `aria-label`
  describing the *action*. A tooltip is not a name.
- Visible label text is contained in the accessible name, in the same order (SC 2.5.3).
- **Colour is never the only signal** (SC 1.4.1) — a second colour is not a second signal.
- Native semantics and keyboard behaviour: Enter/Space activates a button, arrows move
  within a radio group or tab list, a modal traps focus and restores it to the invoker.
- Focus order matches meaning; the focus indicator is visible and unobscured.

Depth and the exceptions: `references/accessibility.md`.

## When systems disagree

Follow the **host platform** of the thing you are building, and say which one you followed.
The real disagreements are named in the references — dialog button order, sentence vs title
case, on-blur vs on-submit validation, checkbox-with-immediate-effect. Do not average two
conventions into a third that belongs to neither.

## Why this skill exists

Rules in the model's context is a *measured* intervention, not a hope. Mowar et al. (W4A
2025) generated 80 banking UIs with GPT-4-turbo and Claude 3.5 Haiku: expert-judged
accessibility violations fell from **58% to 19%** and mean severity from **1.53 to 0.30**
(0–4) when the prompt carried accessibility rules instead of none. Criterion level, same
study: adequate touch targets **32% → 100%**, visible focus indicators **56% → 98%**,
keyboard operability **48% → 94%**, ARIA labels **28% → 95%**. Aljedaani et al. (2024)
found **84%** of ChatGPT-generated websites carried accessibility violations with no such
prompt. A11yn (2025) cut a code model's inaccessibility rate from **0.38 to 0.15**.

No published study isolates a rate for generic button labels, `div`-instead-of-`button`, or
dialog defects in generated UIs — those rules rest on the design systems cited in the
references, not on a measured multiplier. Say so if asked; do not invent a number.
