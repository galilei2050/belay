# The accessibility floor

Everything here is pass/fail with a number or a spec clause behind it. Nothing here is a
preference, and a brand token does not override any of it.

## Target size

| Context | Minimum | Source |
|---|---|---|
| Web pointer target | **24 × 24 CSS px** | WCAG 2.2 SC 2.5.8, level AA |
| Web, preferred | **44 × 44 CSS px** | WCAG 2.2 SC 2.5.5, level AAA |
| Apple touch | **44 × 44 pt** hit region | Apple HIG |
| Material touch | **48 × 48 dp** | Material 3 |

SC 2.5.8's exceptions are specific: sufficient spacing, an equivalent control elsewhere on
the page, inline targets in a sentence, user-agent-controlled presentation, and targets
whose size is essential. "It looked cramped otherwise" is not among them.

The *hit region* is what must reach the size — the visible icon may be smaller, with padding
making up the difference. Do not shrink the target to the glyph's bounds.

## Contrast

| What | Ratio | Source |
|---|---|---|
| Normal text | **4.5:1** | SC 1.4.3 (AA) |
| Large text (≥ 18 pt regular / 14 pt bold) | **3:1** | SC 1.4.3 (AA) |
| UI component boundaries, icons, graphical objects needed to understand content | **3:1** against adjacent colours | SC 1.4.11 (AA) |

This includes **state**: a selected tab, a checked checkbox, a focus ring, an error border.
Disabled controls are exempt from SC 1.4.11 — which is not a licence to communicate through
disabled styling. Test the rendered pairs, in every state and in both themes.

## Name, role, value

Every interactive control exposes a non-empty accessible **name**, the correct **role**, and
its current **state** (SC 4.1.2).

- Prefer a native element. `<button>`, `<a href>`, `<input type=checkbox>` bring role,
  keyboard behaviour and state for free; a `<div onclick>` brings none of them.
- Icon-only control → `aria-label` naming the **action** (`Delete message`), or
  `aria-labelledby` pointing at existing visible text.
- `title` and hover-only tooltips are **not** accessible names.
- **Label in Name (SC 2.5.3, level A):** when a control has visible text, its accessible
  name must *contain* that text, preferably at the start. Visible `Create` →
  `Create a new invoice` passes; `New invoice creation` fails, and voice-control users can
  no longer say the button's name to press it.

## Labels and instructions

- Every form control has a persistent visible label bound with `<label for>` → `id`
  (SC 3.3.2: "Labels or instructions are provided when content requires user input").
- Related radios/checkboxes live in `<fieldset>` with a `<legend>`.
- **A placeholder is not a label.** It disappears on input, usually fails contrast, and
  leaves the field unnamed. Use it for a format example only.
- A visually-hidden label is a last resort for a genuinely compact control such as a search
  field — GOV.UK, Carbon and Atlassian all default to visible.

## Colour is never the only signal

SC 1.4.1 (level A): "Color is not used as the only visual means of conveying information,
indicating an action, prompting a response, or distinguishing a visual element."

A second colour is not a second signal. Error → red border **and** an error message bound to
the field. Status → colour **and** a word or shape. Required → colour **and** `(required)`.
Chart series → colour **and** direct labels or patterns.

## Keyboard and focus

- Everything operable by pointer is operable by keyboard, with no trap (SC 2.1.1, 2.1.2).
- Native behaviour, unmodified: Enter/Space activates a button; Enter follows a link; arrow
  keys move within a radio group, tab list or menu; Esc dismisses.
- **Focus order matches meaning** (SC 2.4.3). Do not repair a visually reordered layout with
  positive `tabindex` — fix the DOM order.
- **A visible focus indicator** on every focusable control (SC 2.4.7), not obscured by
  sticky headers or overlays (SC 2.4.11). The 2 CSS-px perimeter / 3:1 change metric is
  SC 2.4.13 at level AAA — a good implementation target, but do not call it an AA
  requirement.
- **Modal dialogs:** focus moves in, is trapped while open, Esc and the documented dismiss
  both close, and focus returns to the element that opened it.

## Dynamic content

- Announce asynchronous changes that matter — a live region for status, `aria-busy` on a
  region that is loading, focus moved to an error summary on failed submit.
- Do not steal focus for something the user did not initiate.
- Respect `prefers-reduced-motion`; never convey information through motion alone.

## Error prevention for costly actions

WCAG 2.2 SC 3.3.4 (AA) — for pages that create legal commitments or financial transactions,
modify or delete user-controllable data, or submit test responses, at least one of:
**reversible**, **checked** (errors caught with a chance to correct), or **confirmed**
(a review-and-confirm step). See `states.md` for which one to choose.

## What automated checks will not catch

Axe and friends catch missing names, contrast and roles. They cannot tell you that the name
is wrong, that focus order is illogical, that the label lies about what the switch does, or
that an "empty" state is hiding a failed request. A green automated run is a floor, not a
pass — in Mowar et al. (W4A 2025) the automated violation rate barely moved (15.9% → 17.3%)
between accessibility-agnostic and accessibility-oriented prompts, while the expert-judged
rate fell from 58% to 19%. The tool did not see the difference that mattered.

## Sources

W3C WCAG 2.2 (SC 1.4.1, 1.4.3, 1.4.11, 2.1.1, 2.4.3, 2.4.7, 2.4.11, 2.4.13, 2.5.3, 2.5.5,
2.5.8, 3.3.2, 3.3.4, 4.1.2) and the WAI Understanding documents · Apple HIG accessibility ·
Material 3 accessibility (icon buttons, density) · GOV.UK, Carbon and Atlassian form and
focus guidance · Mowar et al., *When LLM-Generated Code Perpetuates User Interface
Accessibility Barriers* (W4A 2025).
