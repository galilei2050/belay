Dispatch subagents in the background, and hand each one a slice it can finish alone. Delegation fails three ways: a session frozen waiting for an agent, an agent that hits a wall mid-task and starts inventing ways around it, and a slice too big to finish whose leftovers you then blame on the agent.

## Background by default

`run_in_background: true`. The harness re-invokes you when the agent finishes, several agents run at once, and you keep working on the parts that never needed its answer. The exception is narrow: you need its result before you can take the *next* action at all.

Never poll for a background agent — no `tail` loop on its output file, no `sleep` until it lands. Waiting in a loop pays the cost of a foreground agent and the cost of a background one. The notification is the signal.

## Prompts say WHAT, not HOW

Give the goal, the constraints, and the paths. Never paste code blocks, exact markup, or a line-by-line plan — the agent reads the codebase and works out the implementation, which is the only reason to spend a subagent on it.

```
# BAD  — "open cache.py, replace line 42 with `ttl = 300`, then add this block: …"
# GOOD — "cache entries expire immediately in prod; find why and fix it. Start at app/cache.py."
```

Hand over what you already know — exact paths, symbols, the command that reproduces it — so its budget goes on reading, not on rediscovering what you could have told it.

## Slice by ownership before dispatching, not after

If the repo restricts which agent may write where (a path-ownership hook, a CODEOWNERS-style rule, a per-agent tool allowlist), **split the work along those lines and dispatch one agent per owner.** A single agent told to "fix the bug and add the test" hits a deny halfway through, and a deny mid-task is where agents start hacking: `sed`, a heredoc, a sibling file, anything that looks like progress.

"Fix bug + regression test" is two agents in parallel, not one. Their work is independent; your job is the seam between them.

## A short answer is your slicing, not the agent's failure

You were told the budget before you chose the slice, so an agent that comes back with part of the
work undone hit a ceiling you picked. Own it: name what is left and dispatch a right-sized agent for
exactly that remainder, then finish.

The tell is your own narration — "агент не успел", "the agent didn't get to it", "агент не доделал",
or quietly hoovering up the leftovers as if that were the plan. Each one files your miss under
someone else's name, and the next slice comes out the same size.

```
# BAD  — "Тесты зелёные. Прогоняю смоук, который агент не успел."
# GOOD — "Тесты зелёные. Смоук остался — нарезал слишком крупно; добиваю."
```

A partial answer is also not a finished task: it's done when the remainder lands, not when the agent
reports (`finish-the-work.md`).

## When a subagent comes back blocked

Do **not** re-dispatch the same agent on the same files, and do not route around the deny yourself. Read who owns the path, dispatch that owner with the remaining work, and let the first agent's partial result stand.

## Why this rule exists

A blocked agent is the most expensive shape in the system: it has already spent its context, it cannot finish, and its next move is almost always a workaround that lands as a mess someone has to unpick. Both halves of this rule exist to stop that — background dispatch so nothing waits on it, and ownership-shaped slices so it never meets a wall it is tempted to climb. Pairs with `finish-the-work.md` (the slice has to be finishable) and `minimal-scope.md` (one owner, one diff).
