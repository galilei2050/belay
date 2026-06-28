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
| [no-shirk-hook](plugins/no-shirk-hook) | Stop | Blocks ending a turn with an ask-instead-of-do question |

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
