---
description: Take the current branch's CI to a verdict, then the PR to merged, deployed and measured
---

Launch the plugin's CI script **in the background** — Bash `run_in_background: true`:

```
timeout -v 6600 python3 ${CLAUDE_PLUGIN_ROOT}/scripts/ci.py ship
```

Keep the `timeout -v` prefix: a detached command with no bound of its own is capped at 1800s, and
the merge stage alone may take twice that — without it the wait dies mid-poll with no verdict.

`ship` is the whole arc in one process: it blocks on the checks, then on the merge, then on
whatever puts the merge in production, and stops at the first stage that is not green. Backgrounded,
it costs the conversation nothing — the harness re-invokes you when it exits, and until then you
answer the user and keep working. A branch takes tens of minutes to reach production; none of them
are minutes the session should spend frozen.

Exit code: `0` shipped, `1` red, `2` still pending at a timeout, `3` nothing to report on (no PR,
no checks, `gh` could not answer). `--branch <name>` inspects a branch other than the checked-out
one; pass it when `$ARGUMENTS` names one.

## The stages on their own

Each is also its own verb, for when you only want that one answer — and each one that blocks is
still a background launch:

```
python3 ${CLAUDE_PLUGIN_ROOT}/scripts/ci.py status    # every check, one line each, right now (fast, foreground)
python3 ${CLAUDE_PLUGIN_ROOT}/scripts/ci.py wait      # block until the checks conclude
python3 ${CLAUDE_PLUGIN_ROOT}/scripts/ci.py logs      # the failing steps only, tail-trimmed (--lines N)
python3 ${CLAUDE_PLUGIN_ROOT}/scripts/ci.py merged    # block until the PR leaves OPEN
python3 ${CLAUDE_PLUGIN_ROOT}/scripts/ci.py deploy    # block on whatever ships the merge commit
```

`wait` blocks inside `gh`'s own watch; `merged` re-asks on a reviewer's clock (`--interval`,
5 min); `deploy` re-lists the merge commit's GitHub Actions runs **and** its Cloud Build builds
until none is still running. Cloud Build is regional and `gcloud`'s default is `global`, so the
region comes from `builds/region`, else `compute/region`, else `--region <name>`; the report always
names the region it searched, because an empty list from the wrong region looks exactly like a
change that never deployed.

Never wrap any of this in a `sleep` loop, a `while` loop, or a Monitor poll. The script already
blocks on the other side, the wait already has a hard cap (`--timeout`), and a hand-rolled loop just
re-pays for the same answer. One background launch, one notification.

## What to do with each verdict

On red: read the log the script printed, name the cause in one sentence, fix it, push, launch `ship`
again. If a stage was still pending when its timeout hit, say so plainly rather than reporting the
branch as done — and if a PR has been sitting unmerged for an hour, that is a person to ask, not a
wait to extend.

On green all the way through, one thing is left that this script cannot read: the service itself.
Read its runtime logs and metrics for real traffic on the path you changed. A finished deploy says
the deploy ran; only the metrics say the change works. Report what you actually observed there,
quoting the numbers.
