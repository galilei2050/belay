---
description: Review the current UI changes with the five usable-ui reviewers
---

Put the UI in the working tree through the panel, without waiting for a commit.

Scope: `git diff HEAD` (staged and unstaged), narrowed to user-facing surfaces. If the user
named a path or a range in `$ARGUMENTS`, use that instead. If nothing user-facing has
changed, say so in one line and dispatch nobody.

Dispatch all five reviewers **in a single message** so they run in parallel. Each is
read-only and reviews the same scope:

- `usable-ui:ui-a11y-reviewer`
- `usable-ui:ui-control-reviewer`
- `usable-ui:ui-state-reviewer`
- `usable-ui:ui-copy-reviewer`
- `usable-ui:ui-layout-reviewer`

Then merge:

- Drop anything a reviewer could not point at a concrete line.
- Report each defect once. The seams overlap on purpose — a `<div onClick>` reaches
  `ui-control-reviewer` as the wrong control and `ui-a11y-reviewer` as a missing name and
  keyboard path. One finding, two reasons.
- Rank by how badly a user is blocked, then by how badly they are misled.
- Fix what survives. Say in one line what you rejected and why.

If every reviewer returns `NO FINDINGS`, say that in one line and stop.
