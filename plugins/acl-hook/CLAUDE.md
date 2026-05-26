# CLAUDE.md — acl-hook

How to improve `hooks/acl_hook.py` without drifting from its purpose.

## Scope (don't expand it)

acl-hook does **one** thing: for every Bash invocation, decide `allow` / `ask` /
`deny`. It does NOT verify tests, check code review, enforce plan adherence,
scan for secrets, or know anything about your project's business logic. Those
belong in separate plugins.

If you find yourself adding logic that needs to read git history, parse a plan
file, hit the network, or call out to a test runner — stop. Wrong plugin.

## The decision rule

Classify every new command (and every new flag combo) into one of three buckets:

- **`allow`** — safe. Read-only inspection, idempotent queries, anything that
  cannot damage state or leak information regardless of arguments. The agent
  should never have to ask the human about these. Examples: `ls`, `cat` (of
  non-`.env*` paths), `git status`, `git log`, `gcloud … list`, `find -name`.

- **`ask`** — needs human audit. The command is **legitimate** but its effect
  is outward-facing, hard to reverse, or context-dependent enough that a
  human's sign-off is the right gate. Examples: `gh pr comment` (outward
  message), `gh issue create`, `npm install` (changes dependency tree),
  `systemctl restart` (affects running services), `curl -X POST` to remote,
  `gcloud … deploy`.

- **`deny`** — destructive, irreversible, or impossible-to-justify in any
  agent context. The reason is shown to the agent; it must redirect, not
  prompt the human. Examples: `git push --force`, `git reset --hard`,
  `git rebase`, `git merge` (merge happens via PR review),
  `gh pr merge` (user-only), `rm` outside the project tree, `sudo`,
  `eval`, `bash -c`.

When in doubt between `ask` and `deny`, pick `ask`. When in doubt between
`allow` and `ask`, pick `ask`. **Friction at the right level is the product;
don't optimise it away by collapsing borderline cases into `allow`.**

## Every deny / ask message must be actionable

The agent reads the `reason` field on `deny` and `ask`. The message must tell
it what to do next. One of these four shapes:

1. **Alternative approach** — "instead of writing a long sed -i expression,
   use the Edit tool: it shows a diff and is reviewable."
2. **Alternative command** — "instead of `git add -A`, list files by path:
   `git add path/one path/two`. Use `git status` first if unsure."
3. **Return to human** — "`gcloud auth login` requires the browser flow that
   the agent can't complete. Ask the user to run it in their terminal."
4. **Restructure the call** — "split the multi-line script into separate Bash
   calls so each step gets its own ACL check and result."

If you can't write a credible "instead, do X" sentence, you don't yet
understand the rule well enough to ship it. Write a real one before merging.

A bad reason is "Not allowed." or "Blocked." A good reason names the
antipattern, explains the failure mode in one clause, and prescribes the fix.

## Where the ACL config lives

The full rule table is **`.claude/acl.json`** inside each project. On the
first Bash invocation in a fresh project, the hook copies its bundled default
(`plugins/acl-hook/hooks/acl_default.json`) to that path; from then on the
project file is authoritative — edit it freely without forking the plugin.

To change rules: edit `.claude/acl.json`. To start over: delete the file and
the next hook run re-installs the bundled default.

## Anatomy of an ACL entry

```json
"git": {
    "rules": [
        {"args": ["push", "--force"], "decision": "deny",  "reason": "…"},
        {"args": ["commit"],          "decision": "allow", "reason": ""}
    ],
    "default": "deny",
    "reason": "git subcommand not in allow-list. Use status/log/diff/… or ask the user."
}
```

- `rules` are checked in order. **First match wins.** Put more-specific deny
  rules before broader allow rules.
- `default` and `default-reason` fire if no rule matches. Defaults are
  themselves an opinion — for `git`, `default: "deny"` means "unknown
  subcommands are denied"; for `cat`, `default: "allow"` means "any path that
  isn't `.env*` is fine."
- Three matcher kinds (don't invent more without a real need):
  - `"args": [a, b, c]` — ordered subsequence. `["commit", "--amend"]` matches
    `git commit --amend` and `git commit -m msg --amend`.
  - `"args_contain": [a, b]` — any of these tokens appears anywhere.
  - `"args_glob": "pattern"` — full arg string matched as one glob.
- The escape hatch: `"fn": "name"` where `name` is a Python callable in
  `CUSTOM_FNS` (registered in `acl_hook.py`). Use only when no pattern matcher
  captures the intent (`curl_mutating_remote`, `all_paths_inside_project`).
  New `fn` predicates require editing `acl_hook.py` — keep them tiny and pure.

## How to add a new rule

Walk through this:

1. **Pick the bucket.** allow / ask / deny — by the rule above. Write the
   actionable reason BEFORE writing the matcher. If you can't write a clean
   reason, your bucket choice is probably wrong.
2. **Pick the matcher.** Prefer `args` (ordered subsequence) — it reads
   closest to how the human would describe the command. Reach for
   `args_contain` only for flag-anywhere patterns (`--no-verify`,
   `--force-with-lease`). `args_glob` is for full-string matches you can't
   express otherwise. `fn` is the last resort.
3. **Position it.** First-match-wins means a `deny` for a specific flag
   combo must come BEFORE the broader `allow` for the bare subcommand.
   See `git add -A` denies above `git add`.
4. **Write a test.** One positive (rule fires) and one negative (rule does
   NOT fire when it shouldn't) — at minimum. Tests live in
   `tests/test_acl_hook.py`. Use the `decide(cmd, logger)` helper.
5. **Run** `pytest plugins/` and `ruff check plugins/`.

## Common mistakes (we've already made these)

- **Allowing too much.** A `default: "allow"` on a new program is convenient
  until someone uses a destructive flag you didn't think about. Default to
  `ask` for any program with mutating subcommands.
- **Vague messages.** "Confirm before doing X" is not actionable. Either it's
  safe (`allow`) or it isn't — and if it isn't, explain what to do.
- **Project-specific logic.** If a rule references `app/`, `tests/`,
  `make backend-wait`, `.work/`, or any path that exists in one specific
  repo, it doesn't belong in this plugin. Either generalise it or move it.
- **Adding a custom predicate to avoid thinking.** Custom `fn` predicates
  are infectious — they accumulate. Before adding one, check whether
  `args_contain` or `args_glob` covers the case.
- **Forgetting first-match-wins.** A new specific deny placed after an
  existing broad allow will never fire. Re-read the ordering after every edit.

## Testing

```
.venv/bin/pytest plugins/acl-hook/tests/ -q
.venv/bin/ruff check plugins/
.venv/bin/mypy --config-file pyproject.toml plugins/acl-hook/hooks/acl_hook.py
```

Conftest pins `PROJECT_DIR` to a tmp dir with `app/`, `tests/`, `infrastructure/`,
`web/`, `tmp/` pre-created, so path-inside-project tests are deterministic.
If you add a path-based rule that depends on a different layout, extend the
fixture, don't hardcode `os.path` calls in tests.

## What this file is not

This is not a tutorial. It assumes you've read `README.md` and skimmed
`acl_hook.py`. If something here is unclear, the source is the authority.
