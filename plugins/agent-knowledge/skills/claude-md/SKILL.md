---
name: claude-md
description: Writes or restructures a CLAUDE.md to the belay skeleton — a dated preamble, a mandatory Business decisions section, a mandatory Technical decisions section, then commands and gotchas, in at most 200 lines. Use when creating a CLAUDE.md, adding to one, migrating an existing one to the skeleton, fixing findings from agent-knowledge, or auditing a repo's CLAUDE.md files ("напиши CLAUDE.md", "обнови CLAUDE.md", "запиши это решение", "audit CLAUDE.md"). NOT for `.claude/rules`, skills, or README files.
---

# Writing a CLAUDE.md

A CLAUDE.md holds what the code cannot say and every task at this level needs. It is loaded
whole, every session, together with every CLAUDE.md above it — each line is paid for each time.

## The skeleton

```markdown
# CLAUDE.md — <what this level is>

Updated: YYYY-MM-DD

## Business decisions

- **<the decision, with its value>** — <why>.

## Technical decisions

- **<the decision>** — <why>. Rejected: <the alternative, and what ruled it out>.

## <anything else this level needs: commands, gotchas, boundaries>
```

Never type the `Updated:` date — the hook stamps it on every edit.

## Business decisions

Values and rules the business owns: prices, hours, thresholds, durations, categories, who may do
what. **Never invent one.** Record only what the user stated or a design doc fixes; if the task
needs a value that is not written anywhere, ask the user, then record the answer here so it is
never asked twice. No decisions at this level → the body is the sentence `None at this level.`

## Technical decisions

Choices a future reader would otherwise reopen: the library, the layer that owns an invariant,
the pattern every module follows. The `Rejected:` clause is the load-bearing part — without it
the alternative gets proposed again next month.

Both sections describe the present. A reversed decision is deleted, not struck through; the
history is in git.

## Where a line belongs

Put a decision in the highest CLAUDE.md whose whole subtree it governs, and only there — a
child repeating its parent is a finding. Before adding any line, route it:

| The line is… | It goes to… |
|---|---|
| true for every task at this level, and costly when violated | this CLAUDE.md |
| about one subdirectory | that directory's CLAUDE.md |
| about one file type | `.claude/rules/<topic>.md` with `paths:` frontmatter |
| a multi-step procedure (release, migration) | a skill |
| something that must happen every time, no exceptions | a hook |
| checkable by a machine | the linter / CI |
| derivable by reading the code, or general programming advice | nowhere — delete it |

`@imports` do not save context: imported files are expanded at launch.

## Wording

Short imperatives with the concrete command or condition. A prohibition names its alternative
("do not edit `generated/` — change the schema and run `make generate`"). Keep `NEVER`/`MUST`
for the few rules where a miss is expensive; emphasis on every line is emphasis on none.

## Migrating an existing file

Sort its content through the routing table first — most over-long files shrink by moving
procedures and directory-specific rules out, not by rewording. Then lift decisions scattered in
prose into the two sections. Do not drop a line you cannot place; ask what it protects.

## Audit

```bash
python3 "${CLAUDE_PLUGIN_ROOT}/hooks/claude_md.py" <file-or-dir>...
```

A directory means its git-tracked CLAUDE.md files. Beyond the hook's checks, the audit reports a
missing `Updated:` line, a file git has never seen, and `stale` — 20+ commits touched the
directory since its CLAUDE.md last changed; reread that file against the code before trusting it.
