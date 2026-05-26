---
name: new-plugin
description: Scaffold a new plugin in this belay marketplace. Use when the user asks to add/create/scaffold a new plugin, hook, skill, or command under plugins/. Creates plugins/<name>/ with the standard layout, fills version 0.1.0, registers it in .claude-plugin/marketplace.json, and adds a row to the README plugin table.
---

# Scaffold a new belay plugin

This is a procedure, not a tutorial. Detailed format/field reference lives in
`docs/AUTHORING.md` — read it once before your first run, then follow the
checklist below. Don't duplicate AUTHORING content into the plugin you're
creating.

## Inputs to collect from the user (ask, don't guess)

- **`name`** — kebab-case, ends in `-hook` if it's a hook plugin.
- **One-sentence description** — what it does, not why it's good.
- **Hook event** (if hook plugin) — one of `PreToolUse`, `PostToolUse`,
  `Stop`, `SubagentStop`, `UserPromptSubmit`, `Notification`, `SessionStart`,
  `SessionEnd`, `PreCompact`. Pick the narrowest event that fits.
- **Matcher** (for PreToolUse/PostToolUse) — usually `Bash`, `Write|Edit`, etc.

If the user is vague on any of these, ask once. Don't invent.

## Checklist

1. **Layout.** Create:
   ```
   plugins/<name>/
   ├── .claude-plugin/plugin.json
   ├── hooks/hooks.json
   ├── hooks/<name>.py
   ├── tests/test_<name>.py
   └── README.md
   ```
   (Drop `hooks/` and add `commands/`, `agents/`, `skills/` instead if the
   plugin isn't a hook. See `docs/AUTHORING.md`.)

2. **`plugin.json`** — version starts at `"0.1.0"`. Include `name`,
   `description`, `author: { "name": "galilei2050" }`.

3. **`hooks.json`** — use `${CLAUDE_PLUGIN_ROOT}` for the path, NOT
   `$CLAUDE_PROJECT_DIR`. Match the event the user picked.

4. **`hooks/<name>.py`** — minimal real implementation:
   - Read JSON from stdin.
   - Decide. Write any user-visible reason to stderr.
   - Exit 0 (allow / no-op) or 2 (block / deny). Don't catch exceptions just
     to silence them — let it crash loud.

5. **`tests/test_<name>.py`** — at least one positive and one negative case.
   Tests are discovered from `plugins/*/tests/test_*.py` (see
   `pyproject.toml`). Use stdlib + pytest; no integration with Claude Code
   itself is needed — call the decision function directly.

6. **`README.md`** of the plugin — three sections: *what it does*, *install*
   (`/plugin install <name>@belay`), *config* (if any).

7. **Register in marketplace.** Append to `plugins` array in
   `/.claude-plugin/marketplace.json`:
   ```json
   { "name": "<name>", "source": "./plugins/<name>", "description": "<one sentence>" }
   ```

8. **Update root README.** Add a row to the plugin table in
   `/README.md` (Name → link to `plugins/<name>`, Type → hook event or
   "Skill"/"Command", What it does → one phrase).

9. **Run `make ci`.** Don't hand control back to the user with a red bar.
   Fix whatever fails. If a test you wrote is wrong, fix the test; if the
   hook is wrong, fix the hook.

10. **Propose a commit message.** Format: `Add <name> plugin: <one phrase>`.
    Don't `git commit` without explicit user confirmation.

## Don't

- Don't put `hooks/` or `commands/` inside `.claude-plugin/` — the loader
  ignores anything in there except `plugin.json` itself.
- Don't copy boilerplate from `acl-hook` wholesale. It's a complex example
  with its own DSL. Start minimal.
- Don't add a `pyproject.toml` or `requirements.txt` inside the plugin
  directory — dependencies are declared in the root `pyproject.toml` dev
  group (or documented as `pip install …` in the plugin's README if truly
  optional).
- Don't bump the version of a plugin you didn't change as part of this
  scaffold task.
