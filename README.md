# belay

A Claude Code plugin marketplace. Small Python plugins that, composed together,
keep the agent on the **plan → implement → verify** rails via lifecycle hooks.

The name is a climbing term: a belay is the rope-and-anchor system that catches
a climber when they fall. These plugins are the belay for the agent.

## Install

```
/plugin marketplace add galilei2050/belay
/plugin install <plugin-name>@belay
```

## Plugins

| Name | Type | What it does |
|------|------|--------------|
| [acl-hook](plugins/acl-hook) | PreToolUse | Gates Bash commands against a project ACL |
| [fs-acl-hook](plugins/fs-acl-hook) | PreToolUse | Gates Write/Edit/Read by path: `.git` off-limits, scratch allowed, no out-of-project writes |
| [branch-guard-hook](plugins/branch-guard-hook) | PreToolUse | Denies file edits while `main`/`master` is checked out — branch first |
| [no-shirk-hook](plugins/no-shirk-hook) | Stop | Blocks ending a turn with an ask-instead-of-do question |
| [delegation-hook](plugins/delegation-hook) | PreToolUse | Subagents run in the foreground only, and each gets a 30 tool-call budget before its tools are cut off |
| [review-panel](plugins/review-panel) | Agents + PreToolUse | Dispatches eight read-only reviewer subagents (correctness, integration, test integrity, explicitness, DRY, bloat, SOLID, comments) over every commit of 64+ changed lines |
| [deep-investigation](plugins/deep-investigation) | Skill + agents | Answers a why-question by building the whole hypothesis tree first, then falsifying branches until the verified mechanisms add up to the observed effect |
| [usable-ui](plugins/usable-ui) | Skill + agents + PreToolUse | Decides UI wording, control, placement and states while UI is written, then dispatches five read-only UI reviewers (copy, control, layout, state, a11y) over the commit |
| [pr-flow](plugins/pr-flow) | Skill + PostToolUse + Stop | Nudges after every commit/push toward a pushed branch and an open PR, refuses to end the turn while either is missing, and writes the PR description (measured failure, mermaid mechanism, checks, risk) |

More plugins will land here as the harness is decomposed.

## Rules

[`rules/`](rules) holds a language-agnostic set of code-smell and agent-behavior
rules (the soft, context layer that complements the enforcement hooks above).
Symlink them into the user-level dir so they load in every project on the
machine:

```
ln -s ~/Projects/belay/rules ~/.claude/rules
```

Rules can't be shipped as a plugin (plugin components are skills/agents/hooks/
MCP/LSP/monitors), so they live here as a plain versioned directory. See
[rules/README.md](rules/README.md).

## Concept

See [docs/PHILOSOPHY.md](docs/PHILOSOPHY.md).

## Authoring a new plugin

See [docs/AUTHORING.md](docs/AUTHORING.md).

## License

[AGPL-3.0](LICENSE). If you run a modified version on a network-accessible
server, you must offer the source to its users.
