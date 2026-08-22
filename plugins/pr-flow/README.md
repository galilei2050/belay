# pr-flow

Carries every commit through to a pushed branch and an open PR with a description a reviewer
can act on — and does not let the agent forget the last two steps.

## What it does

Three components, one per point where the work usually stalls.

| Component | Event | What happens |
|---|---|---|
| `hooks/nudge_after_git.py` | `PostToolUse` on Bash | After a real `git commit` / `git push`, states what the branch still owes: push it, open a PR, or refresh the PR body this push just made stale. Advisory — it can never fail a git command. |
| `hooks/require_pr.py` | `Stop` | Refuses to end the turn while commits sit on this machine only, or a pushed branch has no open PR. Once per (HEAD, step). |
| `skills/pr-description` | Skill | Writes the body: the failure with its measured numbers, a mermaid diagram of the mechanism, what changed, what was verified, risk and rollback. `/pr-flow:pr` runs the whole sequence by hand. |

The nudge fires when the branch is already what the agent is thinking about; the Stop hook is
the backstop for when it is ignored. Both read the repository — `git rev-list --count HEAD
--not --remotes`, `gh pr list --head` — rather than the output of the command that just ran, so
a failed push is not mistaken for a successful one.

### When it stays silent

- On `main` / `master` / a detached HEAD — trunk has no PR to open.
- In a repo with no remote.
- On a branch holding nothing `origin/HEAD` lacks — freshly cut, or merged and still checked
  out. `gh pr create` would have no commits to build a PR from.
- About the PR when `gh` cannot answer (missing, unauthenticated, offline, hung). "No PR found"
  and "I could not look" are different answers, and only the first is worth a nudge. The push
  nudge does not depend on `gh` and still fires.
- On the second Stop over the same HEAD and the same missing step, so an agent that answers a
  refusal in words is not looped.

## Install

```
/plugin install pr-flow@belay
```

Requires `git`, and `gh` (authenticated) for anything PR-related.

## Config

None. State lives in `~/.claude/pr-flow/refused.json` (the Stop hook's loop-breaker); deleting
it just re-arms the refusals.
