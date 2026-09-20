# agent-knowledge

Keeps the knowledge an agent loads lean, current and in one shape. Every line of it is paid for
in context on every session, and a stale line is worse than a missing one. The part covered
today is `CLAUDE.md`.

It keeps every `CLAUDE.md` on one skeleton, so a reader — human or model — always finds the same
three things in the same place: how old the file is, what the business decided, what the
engineering decided. The skeleton itself lives in the
[`claude-md` skill](skills/claude-md/SKILL.md#the-skeleton).

## What it does

**Hook** — `PostToolUse` on `Write|Edit|MultiEdit`, for files named `CLAUDE.md` only. It stamps
today's date into the `Updated:` line (inserting it under the title when absent), lints the
result, and returns the findings as a `block`, so the agent fixes them while the file is open.
A clean file produces no output.

| Check | Finding when… |
|-------|---------------|
| sections | `## Business decisions` or `## Technical decisions` is missing, or has an empty body — `None at this level.` is a valid body |
| length | the file is over 200 lines ([Anthropic's guidance](https://code.claude.com/docs/en/memory)) |
| paths | a `` `code-span` `` path's first segment exists — next to the file, at the repo root, or on disk for a `~/` or absolute span — but the whole path does not. So `origin/main` and `owner/repo` pass, a renamed module does not |
| duplicates | a decision bullet repeats, word for word (case and spacing aside), one from a `CLAUDE.md` in any parent directory — a decision lives at the highest level it governs. A reworded repeat is not caught |
| date | there is no `Updated: YYYY-MM-DD` line — audit only, since the hook has just stamped one |

Headings, dates and paths inside fenced code blocks are examples and are skipped.

**Skill** — [`claude-md`](skills/claude-md/SKILL.md) writes to the skeleton: the entry format for
each section, where a line belongs instead of CLAUDE.md (nested file, path-scoped rule, skill,
hook, linter), and how to migrate an existing file.

**Audit CLI** — the same script with files or directories on argv; exit status 1 on any finding.
It adds the checks that only make sense at rest — see the skill's
[Audit](skills/claude-md/SKILL.md#audit) section.

## Install

```
/plugin install agent-knowledge@belay
```

An existing `CLAUDE.md` that predates the skeleton reports its findings on the first edit after
install. Migrate it with the skill instead of patching finding by finding.

## Config

None — the limits and the section titles are constants at the top of `hooks/claude_md.py`.
