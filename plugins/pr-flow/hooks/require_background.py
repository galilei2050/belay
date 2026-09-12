#!/usr/bin/env python3
"""PreToolUse hook: a blocking `ci.py` verb may only be launched in the background.

`wait`, `merged`, `deploy` and `ship` block for as long as the branch takes to reach production —
minutes on the checks, up to an hour on a merge waiting for a reviewer. In a foreground Bash call
the session is frozen for every one of those seconds: the user cannot ask anything, and the agent
cannot do the work that never depended on the answer. Claude Code already has the right mechanism
— `run_in_background: true`, which re-invokes the agent when the process exits — and the script is
written for it. Nothing but habit puts the call in the foreground.

So this is the one thing in the plugin that blocks rather than advises: the script's own docstring,
its command, its README and the push nudge all say "background", and an agent that skips all four
gets the sentence at the moment it would otherwise stall the conversation.

`status` and `logs` answer in one `gh` call and are deliberately not covered — a hook that made
every reading of CI state asynchronous would just make the state harder to read.
"""

from __future__ import annotations

import json
import re
import sys

# The verbs that block. `status` and `logs` return immediately, so they belong in the foreground.
BLOCKING_VERBS = ("wait", "merged", "deploy", "ship")

# argparse puts the subcommand before its flags, so the verb is the word right after the script.
_CI_VERB_RE = re.compile(rf"\bci\.py\s+({'|'.join(BLOCKING_VERBS)})(?![\w-])")
# Quoted spans are blanked first: a command that merely *talks* about `ci.py ship` — an echo, a
# commit message, a heredoc of instructions — is not a call and must not be denied.
_QUOTED_RE = re.compile(r"'[^']*'|\"[^\"]*\"")

REASON = (
    "`ci.py {verb}` blocks until the branch gets there — a CI run is minutes, a merge waiting on a "
    "reviewer can be an hour — and in the foreground that is the whole session frozen: the user "
    "cannot ask anything and you cannot do the work that never needed the answer.\n\n"
    "Run the same command with Bash `run_in_background: true`. The harness re-invokes you when it "
    "exits, so the wait costs the conversation nothing, and `ship` is one launch for the whole arc "
    "(checks → merge → deploy).\n\n"
    "Do not reach for a substitute instead: a `sleep`/`while` poll loop or a Monitor loop around "
    "`status` pays for the same answer twice and re-freezes the turn. The wait already has a hard "
    "cap (`--timeout`), and `status` / `logs` stay foreground for a state you want right now."
)


def blocking_verb(data: dict[str, object]) -> str | None:
    """The blocking `ci.py` verb this Bash call would run in the foreground, or None."""
    tool_input = data.get("tool_input")
    if data.get("tool_name") != "Bash" or not isinstance(tool_input, dict):
        return None
    if tool_input.get("run_in_background"):
        return None
    command = _QUOTED_RE.sub(" ", str(tool_input.get("command", "")))
    found = _CI_VERB_RE.search(command)
    return found.group(1) if found else None


def main() -> None:
    """PreToolUse entry point: emit one deny, or nothing at all."""
    data = json.loads(sys.stdin.read())
    if data.get("hook_event_name") != "PreToolUse":
        return
    verb = blocking_verb(data)
    if verb is None:
        return
    output = {
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "deny",
            "permissionDecisionReason": REASON.format(verb=verb),
        }
    }
    sys.stdout.write(json.dumps(output) + "\n")


if __name__ == "__main__":
    main()
