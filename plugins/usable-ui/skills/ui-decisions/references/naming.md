# Naming — what each element is called

The class from Step 1 picks the grammar. This file is the per-element detail and the places
the design systems disagree.

## Verb or noun, by element

| Element | Grammar | Example | Source |
|---|---|---|---|
| Button (performs an action) | imperative verb + object | `Create invoice` | Carbon prescribes `{verb} + {noun}` and rejects noun-only button labels; Apple says buttons and links are almost always best labelled with verbs |
| Button (familiar command) | bare verb | `Save`, `Cancel`, `Delete` | Carbon, Shopify — allowed when the object is unmistakable |
| Confirmation button | verb + the object at risk | `Delete account` / `Keep account` | NN/g: the choices summarize what will happen; rejects `OK` and `Yes`/`No` |
| Link (navigates) | noun, or a phrase naming the target | `Billing`, `Learn more about refunds` | Carbon reserves links for navigation |
| Menu item that runs a command | verb phrase | `Duplicate order` | Apple; Shopify action lists |
| Menu item that picks a mode/attribute | noun or adjective | `Compact`, `Plain` | Apple |
| Menu item needing more input | verb phrase + `…` | `Export as…` | Apple — ellipsis means "more input required before this completes" |
| Tab / nav destination | noun phrase naming the content | `Overview`, `Payment methods` | Apple: nouns or short noun phrases; Carbon: describes the contained view, not an action |
| Section heading / card title | entity noun phrase | `Delivery address` | Microsoft: nouns or concise noun phrases |
| Heading of a task page | full instruction | `Choose a delivery method` | GOV.UK question pages — the exception to noun headings |
| Timeline / history entry | object (or actor) + past-tense verb | `SMS sent`, `Alex changed the status` | reports what happened; it is not an invitation |
| Toast confirming a completed action | short noun + verb | `Invoice sent` | Shopify: usually ≤3 words |
| Switch label | noun phrase of the thing controlled | `Email notifications` | Material requires an inline label describing what the switch controls — not `Turn notifications on` |
| Form field label | the requested input, 1–3 words | `Email address` | Carbon |

## Word order

Use the language's normal order and put the differentiating word first.

```
BAD  — `SMS outbound`, `Order create`, `Name customer`
GOOD — `Outbound SMS`, `Create order`, `Customer name`
```

NN/g's eye-tracking work on scanning finds the leftmost first words take the most
attention, which is *why* the differentiator goes first — but front-loading never licenses
breaking grammar. If every row in a list is already an SMS, the word `SMS` carries no
information there at all: promote `Outbound` to a direction column or a status chip and
drop the repetition.

Localize word order. Do not preserve English token order in translation.

## Generic labels — the ban list

`OK` · `Yes` · `No` · `Submit` · `Confirm` · `Click here` · `Continue` (when something more
specific is true) · `Button` · `Learn more` used as the only nav label.

The single exception: Apple permits `OK` on an alert that only informs and offers no
decision. Anywhere a choice is being made, the label states the outcome.

```
BAD  — "Delete 3 files?"  [Cancel] [OK]
GOOD — "Delete 3 files?"  [Keep files] [Delete files]     ← NN/g
```

## Capitalization

**Sentence case** is the modern default across Material, GOV.UK, Shopify, Atlassian, Carbon
and current Microsoft style: capitalize the first word and proper nouns only.

**The disagreement:** Apple uses title-style capitalization for menu items, alert buttons
and tab labels on its native platforms. Legacy Windows guidance also used title case for
commands; current Microsoft style does not. Follow the host platform, and be consistent
within one product. Never ALL CAPS as a way to convey emphasis — it slows reading and, on
some screen readers, is spelled out.

## Length

Buttons 1–3 words (Material); alert actions 1–2 and at most 3 (Apple); form labels 1–3 and
tabs 1–2 (Carbon). These are concision targets, not limits: a longer unambiguous label
beats a short vague one, and translations run 30–40% longer than English.

Never truncate a command so that its consequence is hidden.

## Terminology consistency

One action, one word, product-wide. `Sign in` / `Log in` / `Authenticate` for the same act
is a defect even when each is individually fine. The same applies to the object: if the
data model calls it a *lead*, the UI does not call it a *contact* on one screen and a
*prospect* on the next.

Before inventing a term, grep the codebase for the one already in use.

## Icon-only controls

Use icon-only presentation only for a familiar, unambiguous action or where space is
genuinely constrained. Then:

- Give it an accessible name describing the **action**, not the picture:
  `aria-label="Delete message"`, never `aria-label="Trash can"`.
- On web, add a tooltip that appears on hover **and on keyboard focus** (Material, Carbon).
  A tooltip supplements the name — it never replaces it.
- If a visible text label also exists, the accessible name must contain that text
  (WCAG 2.2 SC 2.5.3).

NN/g favours explicit text wherever close/cancel or the consequence could be ambiguous.

## Error and empty-state wording

Both are covered in `states.md` — the wording rules there (what happened → why → what next;
name the absence then the next action) are naming rules that happen to live with their
states.

## Sources

Apple HIG (buttons, menus, tabs, alerts, writing) · Material 3 (buttons, switch, icon
buttons) · Carbon (button, link, form, tabs) · GOV.UK Design System (button, question
pages) · Shopify Polaris (actions, toast, action list) · Microsoft (labels, capitalization,
UI text) · NN/g (`ok-cancel-or-cancel-ok`, `ui-copy`, `confirmation-dialog`,
`f-shaped-pattern-reading-web-content`).
