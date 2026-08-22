"""Everything this plugin says to the agent, in one place.

Kept apart from the logic because the wording is the product here: the hook's whole job is to
put one sentence in front of the agent at the moment it would otherwise walk away. `_NUDGE`
texts are advisory context after a git command; `_REFUSAL` texts come back as a blocked Stop,
so they are shorter and name the next action first.
"""

SKILL = "pr-flow:pr-description"

PUSH_NUDGE = """\
`{branch}` has {count} commit(s) that exist only on this machine. Push it — \
`git push -u origin {branch}` — and then make sure the branch has a PR in front of it. \
Work that is not on the remote is not reviewable, not deployable, and one disk failure \
from gone."""

PUSH_REFUSAL = f"""\
Do not end the turn yet: `{{branch}}` has {{count}} unpushed commit(s). Push the branch, then \
open its PR (or refresh the existing one) with the `{SKILL}` skill. If you genuinely must \
leave the commits local, say why in one sentence and stop — this refusal fires once."""

OPEN_NUDGE = f"""\
`{{branch}}` is on the remote and has no open PR. Open one now with the `{SKILL}` skill — it \
produces a description a reviewer can act on: what was actually broken (with the numbers you \
measured), a mermaid diagram of the mechanism, what changed, what you verified, the risk and \
the rollback. A bullet list of commit subjects is not a PR description; the diff already says \
that."""

OPEN_REFUSAL = f"""\
Do not end the turn yet: `{{branch}}` is pushed but has no open PR, so nobody can review or \
merge this work. Run the `{SKILL}` skill and open it. This refusal fires once."""

UPDATE_NUDGE = f"""\
PR #{{number}} already covers `{{branch}}` ({{url}}), and you just pushed to it. Re-read its \
body: if this push changed what the PR does, what you verified, or what it leaves open, update \
the description — `gh pr edit {{number}} --body-file <file>`; the `{SKILL}` skill has the shape. \
A description that describes the first commit of a five-commit branch is worse than none. If \
nothing material changed, say so in one line and move on."""
