# Control choice — which widget

Pick from behaviour, never from how it should look. Styling is free; semantics are not.

## Button vs link

| Use | For |
|---|---|
| `<button>` | mutation, submission, state change, opening a dialog/menu, any operation on the current content |
| `<a href>` | navigation to a page, a location on the page, a document, `mailto:`, `tel:` |

A clickable `<div>`/`<span>` is always wrong: it has no role, no keyboard activation, no
focus, and no name. If it must not look like a button, restyle a `<button>`.

**Disagreement:** GOV.UK's `Start now` looks like a button but is an anchor, because it
navigates into the service. Shopify permits a Button component that takes a URL. Resolve it
by behaviour: navigates → link semantics, whatever the paint.

## Checkbox vs switch

| | Checkbox | Switch |
|---|---|---|
| Effect | applied when the form is submitted | **immediate** |
| Count | zero-or-more from a set | one standalone binary setting |
| Extras | consent, acknowledgement, indeterminate/hierarchical selection | has a default/current state |
| Never | — | never next to a `Save` button for the same value |

Material, Microsoft and NN/g all require a switch to take effect immediately. Microsoft
states the distinction in exactly these terms: needs an extra step to commit → checkbox;
changes the running system on flip → switch.

**Disagreement:** Material also permits an on/off checkbox that applies immediately,
especially for a compact group of related desktop options. If you are in Material, prefer
checkboxes for a group and switches for standalone settings; do not mix deferred and
immediate switches in one interface.

Label the switch with the setting, not the instruction: `Email notifications`, not
`Turn email notifications on` — otherwise the label lies in one of its two states.

## Single choice from a list

| Options | Control | Source |
|---|---|---|
| 2–7, comparison matters, space allows | radio group in `<fieldset>` + `<legend>` | Windows gives the 2–7 threshold; GOV.UK requires the fieldset/legend |
| 8+, or space is tight, or comparison does not matter | select | Windows: dropdown at 8+ |
| < 3 | do not use a select | Carbon |
| very long, data-loaded, or free text allowed | combobox / autocomplete | Carbon |
| 2–5 short peers switching a view | segmented control | Material specifies 2–5 |

For 3–7 the systems overlap: choose radios when seeing all options at once helps, a select
when compactness matters more.

Two footnotes: a select must never execute a command (Windows) — it sets a value. And the
HTML `autocomplete="…"` attribute is browser autofill, a different thing from a searchable
combobox; use both, confuse neither. Material 3 Expressive now steers away from the
segmented button toward connected button groups.

## Modal vs inline vs page

- **Inline** — the default for a quick change where the surrounding context stays useful.
- **Modal** — a short, focused, infrequent task or a decision that genuinely must block the
  flow. Carbon's concrete limit: roughly 1–2 fields; do not use a modal beyond four fields
  or when its content has to scroll.
- **Page / sheet / side panel** — complex, long, repeatable, or persistent work.

Never open a modal only to announce success or non-critical information (Apple, Microsoft,
Carbon). A modal costs the user their place; charge that only for a decision.

**Disagreement:** the field-count numbers are Carbon's. Apple, Material, Microsoft and
Atlassian express the same rule as interruption cost vs importance, without numbers.

## Tabs vs accordion vs plain headings

- **Tabs** — a few peer views at the same hierarchy level, one needed at a time, switched
  often. Not for sequential steps, not when users must compare across tabs.
- **Accordion** — several related sections that may be open at once, or tight vertical
  space, or an overview of what exists.
- **Plain headings on a scrolling page** — when most users need most of the content.

GOV.UK warns that content hidden behind either can be missed and asks for research evidence
before using an accordion.

## Notification surface

| Surface | For |
|---|---|
| Inline message | tied to one field, section, or task |
| Banner | page-wide or system-wide condition |
| Toast / snackbar | low-priority, non-blocking confirmation; at most one action (`Undo`, `Retry`) |
| Modal | critical information, or a decision that blocks progress |

If a toast auto-dismisses, its information must also exist somewhere persistent — Material
requires another accessible route to it; a snackbar carrying an action stays until acted on
or dismissed. Shopify specifies at least 10 seconds for accessible toast duration. GOV.UK
forbids replacing validation errors with a notification banner.

## Action hierarchy

- **Exactly one primary action per task context.** Carbon: one primary per screen, not
  counting independent headers, modals and side panels. Atlassian: one primary per *area*,
  which permits several independent task areas. Either way, two competing primaries in one
  area is the defect.
- Secondary for a genuine alternative or a back/cancel; tertiary/ghost for the rest.
- **Destructive styling is semantic, not emphasis.** Red is for serious data loss or
  irreversible effect. `Cancel`, `Back`, and removing an item from a temporary selection are
  not destructive.

## Disabled controls

Do not disable a submit button because required fields are empty (Atlassian is explicit).
The user then has no way to learn what is missing. Let it be pressed; explain on activation.

Disable only for: no permission, an unmet dependency or prerequisite (Carbon), or an
in-flight request — and an in-flight disable must expose a busy/loading state rather than
going quietly inert. GOV.UK notes disabled buttons have poor contrast and prefers
suppressing a duplicate click over disabling ahead of time. If a disabled control stays
visible, say why nearby, in text that keyboard users can reach.

## Sources

Carbon (button, link, select, radio, combobox, modal, accordion, tabs, notifications) ·
Material 3 (switch, checkbox, segmented buttons, dialogs, snackbar) · Microsoft
(toggles, checkbox, radio buttons, drop-down lists, dialogs) · GOV.UK (button, radios,
checkboxes, select, tabs, accordion, notification banner) · Apple HIG (alerts, toggles,
sheets) · Atlassian (buttons, forms, banner, flag) · Shopify Polaris (actions, modal,
toast) · NN/g (`toggle-switch-guidelines`, `checkboxes-vs-radio-buttons`,
`overuse-of-overlays`).
