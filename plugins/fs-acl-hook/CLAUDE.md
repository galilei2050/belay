# CLAUDE.md — fs-acl-hook

Updated: 2026-10-20

How to change `hooks/fs_acl_hook.py` without drifting from its purpose.

## Scope (don't expand it)

fs-acl-hook does **one** thing: for every `Write` / `Edit` / `Read`, decide `allow` /
`ask` / `deny` from the **path**. It's the file-tool half of the same idea acl-hook
applies to Bash. It does NOT lint content, scan for secrets, format, or know your
project's business logic — those are other plugins (or other tools).

The boundary is `$CLAUDE_PROJECT_DIR` (cwd fallback) and the scratch dir is `.scratch/`,
resolved per invocation — so it's universal across repos with zero config. **Keep it
config-free.** A JSON rule-table like acl-hook's is overkill for a handful of path
buckets; don't add one until a real per-project need shows up (then mirror acl-hook's
bundled-default + version-sync pattern).

## The decision rule

Same three buckets as acl-hook, by path:

- **`allow`** — suppresses the prompt. Use for the scratch zone and for reads outside the
  project. Don't `allow` real source edits — those **defer** (return `None`, emit nothing)
  so the user's acceptEdits / review choice still applies.
- **`ask`** — only for a write the human must see: the agent's own permission files.
  Never `ask` on a read.
- **`deny`** — off-limits regardless of context: `.git/` (read or write), writes
  outside the project, and secrets under `~/.claude`. The reason must redirect
  (`.scratch/`, the memory dir, or cd into the repo).

## `~/.claude` is the one out-of-project exception

The blanket out-of-project write deny was wrong in exactly one place: the harness itself asks
the agent to keep memory under `~/.claude/projects/<slug>/memory/`, and that made the memory
directory unreachable. So `~/.claude` is carved out — but not wholesale, because it also holds
the files that decide what the agent may do:

- **secrets** (`.credentials.json`, `.env`, matched by basename anywhere below) — `deny`, both
  directions. There is no task that needs to read a credential file.
- **guards** (`settings.json`, `settings.local.json`, `acl.json`) — `ask` on write, `allow` on
  read. Reading is routine (the `update-config` skill starts there); writing is the agent
  editing its own leash, and the user has to see it. Don't downgrade this to `allow` — a hook
  the agent can silently disable is not a hook.
- **everything else** (memory, logs, plans, jobs, projects) — `allow`, both directions.

The check is `~/.claude` specifically, resolved from `Path.home()`. A project's own
`<project>/.claude` is ordinary in-project config and must keep falling through to the normal
flow — there is a test pinning that, because the two are one character apart in a path and
trivially confused.

`defer` (emit nothing) is the fourth, default outcome — anything in-project that isn't
scratch or `.git`. Don't turn defers into `allow`; that would override the harness.

## Every deny / ask reason must be actionable

The agent reads `permissionDecisionReason`. Each one names what's wrong and prescribes
the fix — see the `_*_REASON` strings. A reason that doesn't tell the agent where
to go instead (`.scratch/`, `git` commands, cd into the repo) isn't
done. Mirror acl-hook's bar.

## Why JSON output, not exit codes

The hook emits the PreToolUse `hookSpecificOutput` JSON (allow/ask/deny), not exit 0/2.
Exit 0 means *defer to normal flow* — which still prompts on a scratch write. To actively
**suppress** the prompt (`allow`) or **escalate** (`ask`) we need the JSON. Verified
against the hooks docs: `permissionDecision: "allow"` bypasses the prompt; hooks take
precedence over `settings.json` permissions.

## Testing

```
pytest plugins/fs-acl-hook/tests/ -q
ruff check plugins/
mypy plugins/
```

Tests call `classify()` directly for per-rule assertions and `main()` via a synthesised
stdin for the emit path. Add one positive and one negative per new rule.

## Business decisions

None at this level.

## Technical decisions

- **Reads outside the project are `allow`, not `ask`** — a read changes nothing, and the prompt stalled background work: task output under `/tmp/claude-<uid>/`, a subagent's screenshot in `/tmp`. The hook's `allow` also skips Claude Code's own out-of-directory prompt. `.git/` and credentials stay denied, and writes outside the project stay denied. Rejected: a carve-out for the task-output directory only, since screenshots and other `/tmp` reads hit the same prompt; a `permissions.allow` rule in settings, which a hook's `ask` overrides.
