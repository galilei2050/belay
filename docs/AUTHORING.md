# Authoring a belay plugin

Plugins in this marketplace are **Python**, single-purpose, and self-contained.
Vendor what you need; don't reach across plugins.

## Layout

```
plugins/your-plugin/
├── .claude-plugin/
│   └── plugin.json          # required
├── hooks/                   # optional
│   ├── hooks.json
│   └── *.py
├── commands/                # optional
│   └── *.md
├── agents/                  # optional
│   └── *.md
├── skills/                  # optional
│   └── your-skill/SKILL.md
└── README.md                # required
```

**Pitfall:** component dirs live at the plugin root, NOT inside
`.claude-plugin/`. Files placed inside `.claude-plugin/` are invisible to the
plugin loader (except `plugin.json` itself).

## plugin.json

Minimum:

```json
{ "name": "your-plugin" }
```

Recommended:

```json
{
  "name": "your-plugin",
  "version": "0.1.0",
  "description": "One sentence. What it does, not why it's good.",
  "author": { "name": "you" }
}
```

`name` must be kebab-case.

## hooks.json

Use `${CLAUDE_PLUGIN_ROOT}` as the path prefix — Claude Code substitutes it
with the installed plugin's absolute path. Don't use `$CLAUDE_PROJECT_DIR`
(that's for project-local hooks, not plugin hooks).

```json
{
  "hooks": {
    "PreToolUse": [
      {
        "matcher": "Bash",
        "hooks": [
          { "type": "command", "command": "python3 ${CLAUDE_PLUGIN_ROOT}/hooks/your_hook.py" }
        ]
      }
    ]
  }
}
```

Hook events available: `UserPromptSubmit`, `PreToolUse`, `PostToolUse`,
`Stop`, `SubagentStop`, `Notification`, `SessionStart`, `SessionEnd`,
`PreCompact`. Pick the narrowest one that does the job.

## Python conventions

- Target Python ≥ 3.10
- Stdlib first. If you need a third-party package, document the `pip install`
  line in the plugin's README. Don't bundle a venv
- Read hook input as JSON from stdin; write any user-visible message to stderr;
  exit code controls the gate (0 = allow, 2 = deny, others vary by event)
- Fail loud: if your hook can't make a decision, deny and explain why on stderr
- Log to a path under `~/.claude/logs/` or the user's choice — don't invent
  your own `~/Logs/` directory

## Add the plugin to the marketplace

Append an entry to `/.claude-plugin/marketplace.json`:

```json
{
  "name": "your-plugin",
  "source": "./plugins/your-plugin",
  "description": "One sentence."
}
```

## Verify

After committing and pushing:

```
/plugin marketplace add galilei2050/belay      # if not already added
/plugin marketplace update belay
/plugin install your-plugin@belay
```

Then exercise the hook in a fresh session and confirm it fires.

## Style: small and sharp

A belay plugin does **one** thing. If you find yourself adding a config flag
to toggle two behaviors, that's two plugins. Composition over configuration.
