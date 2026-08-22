# Layout — where the element goes

Placement rules that are conventions with owners, not aesthetics. Where two platforms
conflict, follow the host and say which.

## Dialog button order — the one everyone gets wrong

| Platform | Order (LTR) |
|---|---|
| Apple | `Cancel` leading, default/confirm **trailing** |
| Material | actions aligned trailing, confirmation closest to the edge; at most two |
| Windows (traditional) | `OK`/command, then `Cancel`, then `Apply` — right-aligned, so confirm sits **left of** Cancel |
| Atlassian | most important dialog action on the right |
| GOV.UK | publishes no general modal order — its rule is the form rule below |

Mirror for RTL. Do not invent a third order, and do not copy the form rule into a dialog.
Limit a focused dialog to two footer actions.

## Form actions

Put the primary submit after the final field, aligned to the form's **leading edge** — left
in LTR (GOV.UK is explicit; Atlassian puts primary first and aligns it with the fields).
Order: primary, then any secondary, then cancel as the last and weakest.

This is the opposite side from most dialog conventions. That is intentional: a form is read
top-to-bottom and the action continues the reading line; a dialog is a decision surface
whose conventions belong to the OS.

## Consequential actions

Separate a destructive action from the frequently-used safe one beside it — physical space
plus a second distinguishing signal (different weight, an icon, different words). NN/g's
rule; the point is that proximity alone causes the misclick, and colour alone does not fix
it. Platform ordering still applies: separate within the convention, don't reverse it.

## Grouping and dividers

Group by **proximity and whitespace first**. Add a divider only when whitespace cannot
carry the boundary:

- between **unrelated** sections or interaction regions → full-width divider
- within a related group that needs subdivision → inset divider
- a repetitive list of like items → margins are usually enough; a line between every row is
  decoration

**Timelines and event feeds:** space *within* one event (its label, actor, timestamp,
payload) is tight; the boundary *between* events gets the larger gap or the divider. If
events group naturally — by day, by conversation, by direction — the group boundary is the
stronger separator and the individual events inside it need only spacing.

Material's guidance: dividers group rather than separate every item, and a dense or
uncontained list is where they earn their place.

## Scanning and reading order

Put the page's purpose, its heading, and the distinguishing words at the top and toward the
leading edge. Use meaningful subheadings and front-loaded list items.

NN/g's F-shaped pattern is a *finding about scanning poorly-formatted text*, not a layout
template — their own research names spotted, layer-cake and committed-reading patterns too,
and better formatting changes the pattern. The popular "Z-layout" is not established by any
of the cited systems. Do not place a confirm button somewhere because of an F or a Z.

DOM order must match visual order: it is the reading order for a screen reader and the
keyboard focus order (see `accessibility.md`).

## Form field layout

- **Label above the input**, visible, and programmatically bound (`<label for>`). GOV.UK and
  Carbon use top-aligned labels for proximity and single-column scanning. Shopify permits
  labels beside fields; left-aligned labels can suit dense desktop forms but cost horizontal
  scanning and complicate responsive layout.
- **Checkbox and radio labels go beside the control**, on the trailing side.
- **One column.** Multi-column forms break the scan and the tab order.
- **Help text before the input; error message adjacent to it** — both bound to the field.

## Required vs optional

Mark the **minority**: if most fields are required, mark the optional ones `(optional)`; if
most are optional, mark the required ones `(required)`. Carbon prescribes this; it minimizes
visual noise.

**Disagreement:** GOV.UK marks optional fields and explicitly forbids the asterisk for
required ones; Atlassian marks required with `*`. Pick one convention per product, state it
at the top of the form if you use `*`, and never mix `*`, `(required)` and red styling.
Requiredness must never be conveyed by colour alone (WCAG SC 1.4.1).

## Density and hierarchy

- One primary action per task area, placed where the task ends, not floating.
- Related controls that act on the same object stay together; a control that acts on the
  page does not sit inside a row that acts on one record.
- A table's row actions belong at the row's trailing edge, consistently, and must reach the
  target-size floor in `accessibility.md`.

## Sources

Apple HIG (alerts) · Material 3 (dialogs, divider guidelines, layout/density) · GOV.UK
(button, text input, question pages, fieldset) · Carbon (forms pattern, checkbox, modal) ·
Atlassian (forms, modal dialog) · Microsoft (dialogs) · NN/g (`f-shaped-pattern…`,
`defeated-by-a-dialog-box`, consequential-action separation).
