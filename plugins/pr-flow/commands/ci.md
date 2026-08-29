---
description: Take the current branch's CI to a verdict, then the PR to merged, deployed and measured
---

Run the plugin's CI script — one blocking call, not a poll loop:

```
python3 ${CLAUDE_PLUGIN_ROOT}/scripts/ci.py wait
```

It returns when the checks conclude (hard cap `--timeout`, 900s by default), prints one line per
check and a verdict, and — only when something failed — the failing steps' log, tail-trimmed.
Exit code: `0` green, `1` red, `2` still pending, `3` nothing to report on (no PR, no checks, or
`gh` could not answer).

- `status` instead of `wait` when you only want the state right now.
- `logs` when a run has already failed and you just need the failure text (`--lines N` for more).
- `--branch <name>` to inspect a branch other than the checked-out one; pass it when `$ARGUMENTS`
  names one.

Never wrap this in a `sleep`/re-run loop and never re-run it "to see if it changed" — `wait`
already blocks on GitHub's side and comes back exactly when there is something new to say.

On red: read the log, name the cause in one sentence, fix it, push, and run `wait` again. If the
run is still pending when the timeout hits, say so plainly rather than reporting the branch as
done.

## Green CI is the middle of the flow, not the end

A branch is finished when the change is live and behaving, so on green keep going:

```
python3 ${CLAUDE_PLUGIN_ROOT}/scripts/ci.py merged    # blocks until the PR leaves OPEN (re-checks every 5 min)
python3 ${CLAUDE_PLUGIN_ROOT}/scripts/ci.py deploy    # blocks on the merge commit's workflow runs
```

`merged` exits `0` merged, `1` closed unmerged, `2` still open at `--timeout` (3600s), `3` if
`gh` could not read the PR. It polls every `--interval` (300s) because GitHub has no watch for a
merge — that sleep belongs inside the script, never in your own loop. It prints the merge commit.

`deploy` then finds the Actions runs for that commit, blocks until they conclude, and reports
them like checks — failing steps' log included. `3` means no Actions run for the commit: this
repo ships some other way, so find out how and watch that instead.

Then read the service itself — its runtime logs and metrics for real traffic on the path you
changed. A green deploy workflow says the deploy ran; only the metrics say the change works.
Report what you actually observed there, quoting the numbers.
