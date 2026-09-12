# pr-flow

Carries every commit through to a pushed branch, an open PR with a description a reviewer can
act on, and — past the green CI where agents stop — a merge, a deploy and the metrics that say
whether the change actually works.

## What it does

| Component | Event | What happens |
|---|---|---|
| `hooks/nudge_after_git.py` | `PostToolUse` on Bash | After a real `git commit` / `git push`, states what the branch still owes: push it, open a PR, or refresh the PR body this push just made stale. |
| `hooks/require_background.py` | `PreToolUse` on Bash | Denies a blocking `ci.py` verb (`wait`, `merged`, `deploy`, `ship`) unless the Bash call sets `run_in_background: true`, and says to relaunch it that way. `status` / `logs` answer immediately and pass. |
| `skills/pr-description` | Skill | Writes the body: the failure with its measured numbers, a mermaid diagram of the mechanism, what changed, what was verified, risk and rollback. `/pr-flow:pr` runs the whole sequence by hand. |
| `scripts/ci.py` | Script | Takes the branch's CI to a verdict — every check on one line, a bounded blocking wait, only the failing steps' log — then the PR to merged and the merge commit to deployed, in GitHub Actions or Cloud Build. Meant to be launched in the background. `/pr-flow:ci` runs it; the push nudge points at it. |

The nudge fires when the branch is already what the agent is thinking about — the commit just
landed, the push just went out — which is the moment the next step is cheapest to take. It is
advisory: `additionalContext` and no decision, so it never fails a git command and never blocks
the agent.

It reads the repository — `git rev-list --count HEAD --not --remotes`, `gh pr list --head` —
rather than the output of the command that just ran, so a failed push is not mistaken for a
successful one.

The one thing here that does decide is `require_background.py`: a blocking `ci.py` verb run in
the foreground is denied, with the sentence that says to relaunch it with `run_in_background:
true`. Everything else in the plugin only ever advises — this one is a hook because "launch it in
the background" is mechanical, checkable from the tool input, and the failure it prevents is the
whole session frozen behind a wait nobody was watching.

## Watching the branch, in the background

```
python3 scripts/ci.py ship      # the whole arc: checks → merge → deploy, one process
```

That is the intended call, and it is intended to run under Bash `run_in_background: true`. The
three answers an agent needs after a push take tens of minutes to arrive and none of them are
needed this second, so one launched process carries the branch from a pushed commit to a live
change and re-invokes the agent once, with the whole story. A foreground wait freezes the session
for the same answer; a `sleep`/`while`/Monitor loop pays for it twice.

`ship` stops at the first stage that is not green — there is nothing to watch downstream of a red
CI or a PR nobody merged — and the stage banners (`── CI ──`, `── MERGE ──`, `── DEPLOY ──`) make
the transcript readable at a glance.

Each stage is also its own verb, for when only one answer is wanted:

```
python3 scripts/ci.py status    # every check, one line each, right now (the one fast verb)
python3 scripts/ci.py wait      # block until the checks conclude, then the verdict (+ log if red)
python3 scripts/ci.py logs      # the failing steps only, tail-trimmed (--lines N)
python3 scripts/ci.py merged    # block until the PR leaves OPEN, re-checking every 5 minutes
python3 scripts/ci.py deploy    # block on whatever ships the merge commit, then judge it
```

Exit codes are the summary: `0` green, `1` red, `2` still pending, `3` nothing to report on (no
PR, no checks, `gh` could not answer — never reported as green).

`wait` blocks inside `gh pr checks --watch`, so it is one call that returns when the answer
changes — not a table redrawn into the agent's context on every refresh. It stops at `--timeout`
(900s) and reports the state as it stands rather than waiting forever. `logs` resolves each
failing check's Actions run out of its URL and prints only the failed steps, last 60 lines per
run, because that is where the traceback is.

## Past the green

Green CI is where an agent reports the work as done, and at that moment nothing has shipped. So
the green verdict itself names the merge, `merged` names the deploy, and `deploy` ends by pointing
at the one thing this script cannot read: the service's own logs and metrics for real traffic.

`merged` polls — GitHub has no watch for a merge — on a reviewer's clock rather than a machine's,
and the sleep lives inside the script instead of in an agent's loop.

`deploy` watches **both** systems that can ship a merge commit:

- **GitHub Actions** — the runs of the merge commit, from `gh run list --commit`.
- **Google Cloud Build** — the builds whose `COMMIT_SHA` substitution is that commit. Builds are
  regional and `gcloud`'s default is `global`, so the region comes from `builds/region`, else
  `compute/region`, else an explicit `--region`; the report always names the region it searched.
  A failed build's log comes from its Cloud Logging resource, not from `gcloud builds log` — that
  command crashes outright on some SDK installs (`KeyError: log_severity.proto`).

Both, not one or the other: a repo whose CI is Actions and whose deploy is Cloud Build has green
Actions runs on the merge commit that say nothing about whether the change is live, and judging
only those is how a queued deploy gets reported as shipped. The wait is one re-listing poll over
both, which is also the only thing that notices a build whose trigger had not fired yet when the
merge landed — the normal case, since `deploy` runs the moment `merged` returns.

Builds and runs are judged by the same code that judges checks: same buckets, same verdict, same
trimmed log on red. A set that is entirely cancelled or skipped is not green — nothing was
deployed — and neither a `gh` nor a `gcloud` that could not answer is ever reported as a repo
that does not deploy.

The flags and the exit codes are in `commands/ci.md`; the reasoning behind each default is
beside it in `scripts/ci.py`.

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

And the background gate stays out of the way:

- For `status`, `logs` and a bare `ci.py` — one `gh` call each, so the foreground is where they
  belong.
- For a command that only mentions a blocking verb inside quotes (an `echo`, a commit message, a
  heredoc of instructions) — talking about the call is not making it.
- For any call that already sets `run_in_background: true`. That is the whole point.

## Install

```
/plugin install pr-flow@belay
```

Requires `git`, and `gh` (authenticated) for anything PR-related.

## Config

None, and no state on disk.
