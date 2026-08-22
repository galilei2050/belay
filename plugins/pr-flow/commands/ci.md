---
description: Take the current branch's CI to a verdict, and read only the log that failed
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

On red: read the log, name the cause in one sentence, fix it, push, and run `wait` again. A
branch is finished when CI is green — not when the push succeeded. If the run is still pending
when the timeout hits, say so plainly rather than reporting the branch as done.
