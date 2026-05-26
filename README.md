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

More plugins will land here as the harness is decomposed.

## Concept

See [docs/PHILOSOPHY.md](docs/PHILOSOPHY.md).

## Authoring a new plugin

See [docs/AUTHORING.md](docs/AUTHORING.md).

## License

[AGPL-3.0](LICENSE). If you run a modified version on a network-accessible
server, you must offer the source to its users.
