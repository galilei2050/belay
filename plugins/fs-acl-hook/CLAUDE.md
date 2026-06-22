# CLAUDE.md — fs-acl-hook

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

- **`allow`** — suppresses the prompt. Use ONLY for the scratch zone, where the agent's
  own throwaways belong. Don't `allow` real source edits — those **defer** (return
  `None`, emit nothing) so the user's acceptEdits / review choice still applies.
- **`ask`** — a legitimate but boundary-crossing op the human should sign off: reading
  another repo. Never `ask` on the agent's own scratch.
- **`deny`** — off-limits regardless of context: `.git/` (read or write), and writes
  outside the project. The reason must redirect (`.scratch/`, or cd into the repo).

`defer` (emit nothing) is the fourth, default outcome — anything in-project that isn't
scratch or `.git`. Don't turn defers into `allow`; that would override the harness.

## Every deny / ask reason must be actionable

The agent reads `permissionDecisionReason`. Each one names what's wrong and prescribes
the fix — see the three `_*_REASON` strings. A reason that doesn't tell the agent where
to go instead (`.scratch/`, `git` commands, cd into the repo, confirm the read) isn't
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
