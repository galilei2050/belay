# CLAUDE.md — acl-hook

How to improve `hooks/acl_hook.py` without drifting from its purpose.

## Scope (don't expand it)

acl-hook does **one** thing: for every Bash invocation, decide `allow` / `ask` /
`deny`. It does NOT verify tests, check code review, enforce plan adherence,
scan for secrets, or know anything about your project's business logic. Those
belong in separate plugins.

If you find yourself adding logic that needs to read git history, parse a plan
file, hit the network, or call out to a test runner — stop. Wrong plugin.

**The one allowed side effect: bootstrapping project state the rules depend on.**
The hook installs `.claude/acl.json` on first run, and `ensure_scratch_dir()`
creates `.scratch/` + adds it to `.gitignore` (so the `rm`-in-scratch rule has a
place to point). These are setup for the decision, not other concerns. Don't add
side effects beyond preparing what the allow/ask/deny decision itself needs.

**Reading trivial git state is OK; running git is not.** A couple of predicates
read ref files directly: `git_push_to_protected_branch` reads `.git/HEAD` (current
branch, for a bare `git push`), and `git_branch_force_delete` reads
`.git/refs/remotes/*` + `.git/packed-refs` (is the branch pushed?, so a recoverable
force-delete doesn't prompt). These are cheap file reads, not subprocesses and not
history. The line stays: no `git log`/`git rev-parse` subprocesses, no network, no
parsing history. If a decision needs more than reading a few ref files, reconsider.

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
  `git push` to main/master (`git_push_to_protected_branch` — branch + PR
  instead), `gh pr merge` (user-only), `rm` outside the scratch dir (see below),
  `sudo`, `eval`, `bash <file>` (but `bash -c '<literal>'` is recursed — below).

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

**Version-gated migration.** When the plugin `version` bumps, the next hook run
*additively* merges in any **wholly-missing command keys** from the bundled
default (tracked via `.claude/.acl-synced-version`). It never rewrites an
existing command's rules — a project override (e.g. `git` set to allow-all)
always wins. If the bundled default has *new rules for a command the project
already defines*, that's logged as `acl_drift` (not auto-applied, since it's
indistinguishable from a deliberate override) — re-sync that command by hand or
delete `.claude/acl.json` to take the fresh default wholesale.

## Waiting / polling: never DENIED, silently BOUNDED

We do **not deny or ask** on a wait loop — that's the bug that dropped the old
`until_loop_with_sleep` / `chained_sleep` detectors: denying made acl-hook a
*second, contradicting voice* (the harness recommends an until-loop, acl-hook
denied it, the agent dead-ended bouncing between them).

But an *unbounded* poll loop is a real leak — `until COND; do sleep N; done`
whose condition never trips (failed deploy, wrong target) runs forever, and a
background loop has no harness timeout to stop it. A leak IS this plugin's scope.
So `wait_loop_unbounded` detects a loop body containing `sleep`, and `main()`
**transparently rewrites** the command to `timeout 600 bash -c '…'` via
`updatedInput` (`WAIT_TIMEOUT_SECONDS`). This is **not a gate**: `permissionDecision`
stays `allow`, no prompt fires, the agent never sees it, and it doesn't contradict
the harness — the loop still runs, just with an upper bound. `updatedInput` does
not re-trigger the hook, so the emitted `bash -c` is never re-evaluated against
the `bash` deny. Already-bounded loops (`timeout … bash -c '…'`) hide their body
inside a quoted word, so the detector skips them — the wrap is idempotent.

The line to hold: **bound, don't block.** Never turn this back into a `deny`/`ask`
on waiting — that's the contradicting-voice bug. A silent `timeout` wrap is the
only acceptable shape.

## `bash -c '<literal>'` is recursed, not blanket-denied

`bash`/`sh` stay `deny` by default, but `check_command` first calls
`_extract_shell_c`: for the exact `<shell> -c '<script>'` shape with a
**fully-literal** script (no `$…`/backtick — those are non-literal and can't be
statically vetted, so they keep the deny), it re-runs the full pipeline on the
script as if typed directly. So `bash -c 'git status'` → allow, `bash -c 'rm -rf
/etc'` → deny. This keeps smuggling blocked while letting the bounded
`timeout … bash -c '…'` form (and simple literal scripts) through.

## `rm`: allowed only in the scratch dir, never `ask`

`rm` has exactly two outcomes — `allow` inside the scratch dir `.claude/tmp/`,
`deny` everywhere else — and **never `ask`**. An `ask` on `rm` is the worst
shape: it interrupts the human for the agent's own cleanup. So the agent gets a
sanctioned scratch area where rm / `rm -rf` are free (no prompt), and is denied
everywhere else with a message that says exactly that.

- `all_paths_under_scratch` (in `acl_hook.py`) is the allow predicate: every
  non-flag path must resolve under `<project>/.scratch/`. `resolve()` collapses
  `..`, so a traversal out of scratch falls through to deny.
- Real in-tree source files are **not** an allow anymore (they used to be). The
  deny message tells the agent: scratch goes in `.scratch/`; a tracked file
  that should be removed is left for the user to delete, so the removal stays
  visible in review instead of vanishing under a silent `rm`.
- `rmdir` is untouched — it only removes *empty* dirs (no data loss), so it
  keeps the `all_paths_inside_project` allow.

**Why `.scratch/` and not `/tmp` or `.claude/tmp/`.** Three constraints, one
location satisfies all:
- *In-tree* → the Write tool creates files there with no edit prompt; `/tmp` is
  out-of-tree and prompts on every Write.
- *Not under `.claude/`* → the harness guards edits to the agent's own config
  dir and prompts for them, so `.claude/tmp/` defeated the no-prompt goal.
- *Hidden, uncommon name* → won't collide with a project's own `tmp/`/`build/`.

**Why it's universal across repos** (the design requirement): the predicate
resolves `<PROJECT_DIR>/.scratch/` from the per-invocation `PROJECT_DIR`, so
"rm allowed in `.scratch/`" automatically means *this* repo's scratch in every
repo — zero per-repo config. And the plugin **owns the dir it polices**:
`ensure_scratch_dir()` runs in `main()` on every Bash invocation and idempotently
(a) `mkdir`s `.scratch/` (recreating it if a prior `rm -rf` removed it) and
(b) appends `.scratch/` to *this repo's* `.gitignore` if absent. So the agent
never `mkdir`s it, never edits `.gitignore`, and is never prompted for either —
it just writes scratch files and rm's them. This is a deliberate, documented
side effect (see Scope below), the same shape as the first-run `.claude/acl.json`
bootstrap: the hook sets up the project state its rules depend on.

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
