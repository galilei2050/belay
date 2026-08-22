# States — what the screen does when it is not the happy path

A screen owes five states. Each one is a design decision, and each one is a place agents
routinely ship nothing at all.

## Loading

NN/g's three limits, unchanged since they were measured:

| Wait | What the UI owes |
|---|---|
| ≤ 0.1 s | feels instantaneous — no feedback beyond the result |
| ~1 s | flow of thought is preserved; show the state change if the result is not otherwise visible |
| 1–10 s | continuous activity feedback; the user is still on this task |
| > 10 s | determinate progress or an estimate, plus a way to cancel or send it to the background |

**Disagreement:** component-level thresholds are newer and tighter. Material: no indicator
under 200 ms, a loading indicator 200 ms–5 s, a progress indicator beyond 5 s. Carbon: show
a loading indicator when the expected wait exceeds 3 s. Use the component threshold for a
component and NN/g's limits for a whole task.

**Skeleton vs spinner:** skeleton for the initial load of a predictable layout, where
holding the geometry prevents layout shift — and only for a few seconds. Spinner for an
isolated, indeterminate action. Determinate bar when progress is measurable or the wait is
long. Carbon: never skeletonize toasts, dropdown items, modals or loaders; a modal's
*contents* may be skeletonized, its shell may not.

Implementation, not optional: keep the triggering context visible, prevent a duplicate
submission, mark the busy region programmatically (`aria-busy`, or a `progressbar` role with
a label naming what is loading). Animation alone tells a screen-reader user nothing.

## Empty

There are five empties and they need different copy:

| Kind | Says | Offers |
|---|---|---|
| First use | what this will hold and why it is useful | the creating action: `Create project` |
| Genuinely none | there are none right now | the creating action, if permitted |
| No results (search) | nothing matched *this query* | broaden, correct, or clear the search |
| Filtered out | rows exist but the filter hides them | `Clear filters` — and say how many are hidden |
| No permission | you do not have access | who to ask — **not** a create button |

Never a bare `No data` / `Empty` / `—`. And never show an empty state when the truth is
"still loading" or "the request failed": an empty result that cannot be told apart from a
failed one is the defect, not the blank.

## Error

**What happened → why, if it helps → what to do next.** Microsoft prescribes problem,
probable cause, remedy, and forbids blaming the user. NN/g: concise, precise, constructive.
GOV.UK: what went wrong and how to fix it.

```
BAD  — "Error 0x80070005"        · "Invalid input"     · "You entered an invalid email"
GOOD — "That email address isn't valid — check for typos and try again."
       (diagnostic code, if any, after the human sentence — never instead of it)
```

Preserve everything the user typed. Never make them re-enter a form because one field
failed.

**Field errors:** put the message adjacent to the field, bind it programmatically, and use
the field's own words — `Enter an email address`, not `Invalid input`. For a submitted page,
GOV.UK also requires an error summary at the top, linking to each invalid field, with
wording identical to the inline messages, and focus moved to the summary.

**Validation timing:** never mark a field wrong while it is still being typed, and never on
an untouched field. State the constraint *before* input.

**Disagreement:** NN/g and Carbon accept live validation *after* the user leaves the field,
clearing the error as soon as it is corrected. GOV.UK says do not validate on blur at all —
wait for `Continue`. Use GOV.UK's model for government and research-backed accessibility
contexts, the on-blur model elsewhere; do not do both in one product.

## Partial and stale

If half the data loaded, or the figures are from five minutes ago, the user must be able to
tell without asking. A section that failed shows its own inline error and the rest still
works. A cached number carries its timestamp. Silently rendering partial data as complete
is the same class of bug as an empty state that hides a failure.

## Success

The result is visible where the user is looking, or it is confirmed there. A toast that
auto-dismisses is not confirmation on its own — the changed state has to be in the UI.
Do not open a modal to announce success.

## Destructive actions — undo or confirm

**Prefer act-now plus a durable `Undo`** when reversal is reliable and the blast radius is
small. It is faster for the common case and it does not train the user to click through
dialogs.

**Confirm instead when** the action is irreversible, bulk, security-sensitive, expensive, or
unexpected in context. Then:

- the title names the action and the object: `Delete 3 files?`
- the buttons restate it: `Delete files` / `Keep files` — never `OK`/`Cancel`
- state the consequence: `This can't be undone.`
- for the truly dangerous, require a non-routine act — typing the resource name.

**Disagreement:** "undo is preferred" is not absolute. Apple warns for *unexpected and
irreversible* loss but not where loss is the expected result of the command. NN/g requires
confirmation for serious or irreversible consequences and a nonstandard confirmation for
particularly dangerous ones. WCAG 2.2 SC 3.3.4 (AA) makes this a legal floor for actions
that create legal commitments, move money, or delete user data: they must be **reversible**,
**checked**, or **confirmed** — at least one of the three.

If the action is reversible, say so where the button is; the promise of undo is what makes
the missing dialog acceptable.

## Sources

NN/g (`response-times-3-important-limits`, `confirmation-dialog`, `user-control-and-freedom`,
error-message guidance, inline validation) · Material 3 (progress indicators, snackbar,
dialogs) · Carbon (loading pattern, skeleton, notifications, forms) · GOV.UK (error message,
error summary, notification banner, validation) · Microsoft (Windows UX messages) · Apple
HIG (alerts, feedback, undo and redo) · Atlassian (empty state, messages) · Shopify Polaris
(toast, undo) · W3C WCAG 2.2 SC 3.3.4.
