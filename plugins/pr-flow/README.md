# pr-flow

Carries every commit through to a pushed branch, an open PR with a description a reviewer can
act on, and — past the green CI where agents stop — a merge, a deploy and the metrics that say
whether the change actually works.

## What it does

| Component | Event | What happens |
|---|---|---|
| `hooks/nudge_after_git.py` | `PostToolUse` on Bash | After a real `git commit` / `git push`, states what the branch still owes: push it, open a PR, or refresh the PR body this push just made stale. |
| `skills/pr-description` | Skill | Writes the body: the failure with its measured numbers, a mermaid diagram of the mechanism, what changed, what was verified, risk and rollback. `/pr-flow:pr` runs the whole sequence by hand. |
| `scripts/ci.py` | Script | Takes the branch's CI to a verdict — every check on one line, a bounded blocking wait, only the failing steps' log — then the PR to merged and the merge commit to deployed. `/pr-flow:ci` runs it; the push nudge points at it. |

The nudge fires when the branch is already what the agent is thinking about — the commit just
landed, the push just went out — which is the moment the next step is cheapest to take. It is
advisory: `additionalContext` and no decision, so it never fails a git command and never blocks
the agent.

It reads the repository — `git rev-list --count HEAD --not --remotes`, `gh pr list --head` —
rather than the output of the command that just ran, so a failed push is not mistaken for a
successful one.

## Watching CI

```
python3 scripts/ci.py wait      # block until the checks conclude, then the verdict (+ log if red)
python3 scripts/ci.py status    # every check, one line each, right now
python3 scripts/ci.py logs      # the failing steps only, tail-trimmed (--lines N)
```

Exit codes are the summary: `0` green, `1` red, `2` still pending, `3` nothing to report on (no
PR, no checks, `gh` could not answer — never reported as green).

`wait` blocks inside `gh pr checks --watch`, so it is one call that returns when the answer
changes — not a `sleep` loop, and not a table redrawn into the agent's context on every refresh.
It stops at `--timeout` (900s) and reports the state as it stands rather than waiting forever.
`logs` resolves each failing check's Actions run out of its URL and prints only the failed steps,
last 60 lines per run, because that is where the traceback is.

## Past the green

```
python3 scripts/ci.py merged    # block until the PR leaves OPEN, re-checking every 5 minutes
python3 scripts/ci.py deploy    # block on the merge commit's workflow runs, then judge them
```

Green CI is where an agent reports the work as done, and at that moment nothing has shipped. So
the green verdict itself names `merged`, `merged` names `deploy`, and `deploy` ends by pointing
at the one thing this script cannot read: the service's own logs and metrics for real traffic.

`merged` is the only verb here that polls — GitHub has no watch for a merge — and it polls on a
reviewer's clock (`--interval`, 300s) up to `--timeout` (3600s), so the sleep lives inside the
script instead of in an agent's loop. Exit codes: `0` merged, `1` closed unmerged, `2` still open
at the timeout, `3` `gh` could not read the PR. It prints the merge commit.

`deploy` looks up the Actions runs for that commit, blocks in `gh run watch` on the ones still
going, and reports them exactly like checks — same buckets, same verdict, same trimmed log on
red. `3` means there was no Actions run for the commit: the repo ships some other way, and
saying so beats reporting an undeployed change as green.

### When it stays silent

- On `main` / `master` / a detached HEAD — trunk has no PR to open.
- In a repo with no remote.
- On a branch holding nothing `origin/HEAD` lacks — freshly cut, or merged and still checked
  out. `gh pr create` would have no commits to build a PR from.
- About the PR when `gh` cannot answer (missing, unauthenticated, offline, hung). "No PR found"
  and "I could not look" are different answers, and only the first is worth a nudge. The push
  nudge does not depend on `gh` and still fires.
- On a `--dry-run`, on a `git -C <other repo>` command, and on `git commit` written inside a
  quoted string — each is judged per command segment, not per whole Bash call.

## Install

```
/plugin install pr-flow@belay
```

Requires `git`, and `gh` (authenticated) for anything PR-related.

## Config

None, and no state on disk.
