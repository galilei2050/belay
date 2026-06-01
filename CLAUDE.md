# CLAUDE.md — belay

Repo-wide invariants for the agent. This file is policy, not tutorial. For
how-to detail, follow the links.

## What this repo is

A Claude Code plugin marketplace. Each plugin under `plugins/<name>/` is a
single-purpose Python hook (or skill/command/agent), self-contained, registered
in `.claude-plugin/marketplace.json`. Philosophy: `docs/PHILOSOPHY.md`.
Authoring detail: `docs/AUTHORING.md`.

## Before any commit: run `make ci`

`make ci` = `lint` + `typecheck` + `test`. **Run it locally before proposing
a commit.**

The git hooks split the work across two stages (see `.pre-commit-config.yaml`):

- `pre-commit` → `make pre-commit` (= `lint-fix`: auto-format + ruff fix +
  `anon_lint.py`). Fast, runs on every `git commit`.
- `pre-push` → `make pre-push` (= `typecheck` + `test`). Runs on `git push`.

So `git commit` succeeding does NOT mean tests/typecheck pass — those only
run on push, and only if the user installed pre-push hooks (`make setup`
does, but not every clone has it). Treat `make ci` as the real gate; don't
lean on the hooks.

If `make ci` fails, fix it. Don't commit red, don't `--no-verify`, don't ask
the user to fix CI on their side.

## Bump plugin `version` on functional changes

When you change a plugin's behavior, bump `version` in
`plugins/<name>/.claude-plugin/plugin.json` in the same commit. Semver:

- **patch** — bugfix, no behavior change for valid input
- **minor** — new rule, new flag, additive behavior
- **major** — breaking change (removed rule, renamed config key)

Documentation-only edits (`README.md`, comments) don't bump the version. If
you touched the plugin's executable code or its `hooks.json`, bump it.

Don't batch a version bump into a later commit. The version is part of the
change.

## Adding a new plugin

Use the `new-plugin` skill — it auto-triggers when the user asks to add a
plugin. It scaffolds the standard layout, fills `version: "0.1.0"`, registers
the plugin in `marketplace.json`, and updates the table in `README.md`.

If you must do it by hand, follow `docs/AUTHORING.md` step by step. The two
easy-to-miss steps:

1. Append the plugin entry to `.claude-plugin/marketplace.json`.
2. Add a row to the plugin table in the root `README.md`.

## Plugin-local CLAUDE.md

If a plugin has non-obvious internal policy (e.g. how to add new rules, what
"allow vs ask vs deny" means in its DSL), that goes in **its own**
`plugins/<name>/CLAUDE.md`. See `plugins/acl-hook/CLAUDE.md` for the shape.

This file (root `CLAUDE.md`) is for things that apply across the whole repo.
Don't bloat it with plugin-specific lore.

## What NOT to put in this file

- Plugin-internal logic — that's `plugins/<name>/CLAUDE.md`.
- Authoring walkthroughs — that's `docs/AUTHORING.md`.
- Project philosophy — that's `docs/PHILOSOPHY.md`.
- Anything that would rot when one plugin changes.
