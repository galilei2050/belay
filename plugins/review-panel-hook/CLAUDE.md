# CLAUDE.md — review-panel-hook

How to change the hook and the panel without breaking either.

## Scope (don't expand it)

The hook decides **one** thing: is this Bash call a commit whose content the panel has not
seen yet? It does NOT review code, call an LLM, read the transcript, parse the diff, or
know anything about the reviewers beyond their names. All the judgment lives in
`agents/*.md`, which are prompts, not code.

If a change would make the hook *understand* the diff, it belongs in a reviewer prompt
instead.

## Advisory is the design, not a weaker version of a gate

The hook emits `additionalContext` and **never** a `permissionDecision`. Two reasons, and
both matter:

1. `permissionDecision: "allow"` bypasses the permission system entirely — it would
   override acl-hook and the user's own settings for every commit. A reviewer panel has no
   business handing out that authority.
2. `deny` would make the commit itself the gate. That was considered and rejected: it puts
   five subagents between the agent and every `git commit`, including trivial ones, and it
   inverts belay's own rule that reversible local work is never a reason to stop.

Don't "upgrade" this to a blocking gate. If a blocking review gate is wanted, it is a
different plugin with a verification artifact (see `docs/PHILOSOPHY.md`), not a flag here.

## Why the digest exists

Without it, a commit rejected by `pre-commit` and retried re-dispatches five subagents over
byte-identical content. The digest is over the code under review, not the command, so:

- retry of the same content → silent
- agent fixed something and re-committed → new content, panel runs again
- `git commit -a` → the digest comes from `git diff HEAD`, since `-a` stages at commit
  time and the index is still empty when the hook fires

Keep the state file dumb (repo path → digest). It is a de-duplicator, not a review log; if
you ever need history, that is a log under `~/.claude/logs/`, not this file.

## Changing the panel

**The ceiling is five seats.** Every added reviewer costs a subagent run on every commit,
and overlapping roles produce the same finding five times, which trains the agent to skim
the report. Adding a seat means arguing another one out.

To add or change a role:

1. **Justify it with measured evidence** — an AI-vs-human rate from a real study, not a
   plausible-sounding smell. The README table carries each seat's number; a new seat needs
   its own row. "This seems bad" is not a seat.
2. **Give it an exclusive lane.** Write the *Not your lane* section first, naming which
   existing reviewer takes each adjacent smell. If you cannot draw that line, the role is a
   duplicate of one that already exists.
3. **Keep the prompt self-contained.** Reviewers must work in a repo with no `rules/`
   directory. Yes, that duplicates wording with `rules/*.md` — that trade was made
   deliberately so the plugin installs anywhere. Don't "fix" it by reaching out to `rules/`.
4. **Keep it read-only.** `disallowedTools: Write, Edit, NotebookEdit`. A reviewer that
   edits is no longer a reviewer, and the main agent loses the merge-and-decide step.
5. Add the name to `REVIEWERS` in the hook, add the README row, bump the plugin version.

The two lanes that are easiest to blur, so state them explicitly in any new prompt:
**`bloat-reviewer` judges size, `solid-reviewer` judges placement.** A 200-line function is
bloat; a function doing two jobs that belong in different modules is SOLID.

## What every reviewer prompt must end with

`NO FINDINGS` on a clean diff, verbatim and alone. The main agent merges five reports; a
reviewer that pads a clean result with observations makes the merge unreadable and teaches
the agent to ignore the panel.

## Testing

```
uv run pytest plugins/review-panel-hook/ -q
uv run ruff check plugins/review-panel-hook/
uv run mypy plugins/review-panel-hook/hooks/review_panel_hook.py
```

The `repo` fixture builds a real throwaway git repo, so the digest and end-to-end paths run
against actual `git`, not a mock — the hook's whole job is reading git state, and a mocked
git would test nothing. `state_file` redirects the dedupe file into `tmp_path`; never let a
test touch the real `~/.claude`.
