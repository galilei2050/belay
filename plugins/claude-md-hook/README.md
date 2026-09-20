# claude-md-hook

Keeps every `CLAUDE.md` on one skeleton, so a reader — human or model — always finds the same
three things in the same place: how old the file is, what the business decided, what the
engineering decided.

```markdown
# CLAUDE.md — <what this level is>

Updated: YYYY-MM-DD

## Business decisions
## Technical decisions
## <commands, gotchas, …>
```

## What it does

**Hook** — `PostToolUse` on `Write|Edit|MultiEdit`, for files named `CLAUDE.md` only. It stamps
today's date into the `Updated:` line (inserting it under the title when absent), lints the
result, and returns the findings as a `block`, so the agent fixes them while the file is open.
A clean file produces no output. The date is stamped rather than typed because a hand-kept date
stops moving on the second edit, and the model reading the file has no `git log` to tell it the
file is old.

| Check | Finding when… |
|-------|---------------|
| sections | `## Business decisions` or `## Technical decisions` is missing, or has an empty body — `None at this level.` is a valid body |
| length | the file is over 200 lines ([Anthropic's guidance](https://code.claude.com/docs/en/memory)) |
| paths | a `` `code-span` `` path's first segment exists (next to the file or at the repo root) but the whole path does not — so `origin/main` and `owner/repo` pass, a renamed module does not |
| duplicates | a decision bullet repeats one from a `CLAUDE.md` in any parent directory — a decision lives at the highest level it governs |

Headings and paths inside fenced code blocks are examples and are skipped.

**Skill** — [`claude-md`](skills/claude-md/SKILL.md) writes to the skeleton: the entry format for
each section (`Rejected:` alternatives for technical decisions; business values come from the
user, never invented), where a line belongs instead of CLAUDE.md (nested file, path-scoped rule,
skill, hook, linter), and how to migrate an existing file.

**Audit CLI** — the same script with paths on argv; a directory means its git-tracked files.
Exit status 1 on any finding.

```bash
python3 plugins/claude-md-hook/hooks/claude_md_hook.py ~/Projects/nisse
```

The audit adds one check that only makes sense at rest: `stale`, when 20+ commits touched a
directory since its `CLAUDE.md` last changed.

## Install

```
/plugin install claude-md-hook@belay
```

An existing `CLAUDE.md` that predates the skeleton reports its findings on the first edit after
install. Migrate it with the skill instead of patching finding by finding.

## Config

None. The limits (200 lines, 20 commits) and the two section titles are constants at the top of
`hooks/claude_md_hook.py`.
