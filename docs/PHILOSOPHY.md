# Philosophy: a harness via hooks

Claude Code is good at three things separately — making a plan, writing code,
and verifying that the code does what the plan said — but it is not naturally
disciplined about doing them **in order, every time, without skipping**. Left
to its own devices it will short-circuit: implement before the plan is locked,
declare "done" before verification ran, edit files outside the plan's scope,
quietly rewrite git history to make a problem disappear.

The standard fix is to add prompting and hope. That works ~70% of the time and
fails silently the other 30%. Silent failures are the worst kind because the
agent reports success.

**belay takes a different tack: keep the agent on the rails with hooks, not
prompts.** Hooks run outside the model's context; they cannot be talked out of
their decision. If the rule is "no commits without a green verification
artifact for this branch," the hook reads the filesystem, sees no artifact, and
denies the `git commit`. The agent learns by being told "no," not by being
asked to remember.

## The plan-implement-verify shape

```
   ┌──────┐    ┌───────────┐    ┌────────┐
   │ plan │ →  │ implement │ →  │ verify │
   └──────┘    └───────────┘    └────────┘
       ▲                              │
       └──────────────────────────────┘
```

Each phase has its own hooks:

- **plan** — read-only gates so the agent cannot edit code while planning;
  guards that prevent the plan from being modified once approved
- **implement** — ACL on Bash, scope guards that block edits outside the plan's
  declared file set, agent-dispatch tracking so subagents stay accountable
- **verify** — gates on `git commit` and "done" claims that demand the
  verification artifact exists and is fresh; code-review gates that demand a
  reviewer's pass before merge

## Why a marketplace, not a single plugin

The harness is ~25 hooks today. Bundling them as one monolithic plugin makes it
hard for other projects to adopt one piece (say, just the Bash ACL) without
inheriting the rest. The marketplace lets each hook be installed independently
and composed à la carte. Some plugins will be standalone; others will declare
that they expect siblings to be present.

## Composition over configuration

Where most plugin systems try to make one plugin do many things via config,
belay prefers many small plugins each doing one thing. A new constraint is a
new plugin, not a new config key in an existing one.
