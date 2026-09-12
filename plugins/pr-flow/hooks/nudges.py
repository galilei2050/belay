#!/usr/bin/env python3
"""Everything this plugin says to the agent, in one place.

Kept apart from the logic because the wording is the product here: the hook's whole job is to
put one sentence in front of the agent at the moment it would otherwise walk away.

Keyed by `Step.kind` and formatted with the whole step, so a text uses whichever fields it
needs and ignores the rest.
"""

from pathlib import Path

SKILL = "pr-flow:pr-description"

# Absolute, and resolved here: a hook's `additionalContext` is text, so nothing expands
# `${CLAUDE_PLUGIN_ROOT}` after we emit it, and a relative path breaks the moment the agent runs
# from a subdirectory. The agent must be able to paste the line as-is.
CI = f"python3 {Path(__file__).resolve().parent.parent / 'scripts' / 'ci.py'}"

# The one line every nudge ends on. `ship` is the whole arc — checks, then the merge, then the
# deploy — in a single process, and it is named as a background launch because the alternative is
# what agents actually do: block the session on a foreground wait, or give up and report a pushed
# branch as done.
CHAIN = (
    f"`{CI} ship` is that whole arc in one call — it blocks on the checks, then on the merge, then "
    "on whatever ships the merge (GitHub Actions runs and Cloud Build builds alike), and stops at "
    "the first stage that is not green. Launch it with Bash `run_in_background: true` and carry on "
    "talking: the harness re-invokes you when it exits, so waiting costs the conversation nothing "
    "and there is no reason to sit in a foreground wait or a `sleep` loop. When it comes back "
    "green, one thing is still unread — the service's own logs and metrics for real traffic. A "
    "finished deploy says the deploy ran, not that the change works. Do not stop before that."
)

NUDGES = {
    "push": (
        "`{branch}` has {unpushed} commit(s) that exist only on this machine. Push it — "
        "`git push -u origin {branch}` — and then make sure the branch has a PR in front of it. "
        "Work that is not on the remote is not reviewable, not deployable, and one disk failure "
        "from gone."
    ),
    "open": (
        "`{branch}` is on the remote and has no open PR. Open one now with the `" + SKILL + "` "
        "skill — it produces a description a reviewer can act on: what was actually broken (with "
        "the numbers you measured), a mermaid diagram of the mechanism, what changed, what you "
        "verified, the risk and the rollback. A bullet list of commit subjects is not a PR "
        "description; the diff already says that. Then find out what happens to it: " + CHAIN
    ),
    "update": (
        "PR #{number} already covers `{branch}` ({url}), and you just pushed to it. Re-read its "
        "body: if this push changed what the PR does, what you verified, or what it leaves open, "
        "update the description — `gh pr edit {number} --body-file <file>`; the `" + SKILL + "` "
        "skill has the shape. A description that describes the first commit of a five-commit "
        "branch is worse than none. If nothing material changed, say so in one line and move on. "
        "Either way this push started a CI run you have not seen, and a branch is not finished "
        "when the push succeeded — nor when CI goes green: " + CHAIN
    ),
}
