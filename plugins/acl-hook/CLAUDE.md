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
`ensure_scratch_dir()` creates `.scratch/` + adds it to `.gitignore` (so the
`rm`-in-scratch rule has a place to point). That's setup for the decision, not
another concern. Don't add side effects beyond preparing what the allow/ask/deny
decision itself needs — in particular, the hook writes no config into the project.

**Reading trivial git state is OK; running git is not.** Several predicates
read ref files directly: `git_push_to_protected_branch` reads `.git/HEAD` (current
branch, for a bare `git push`), `git_branch_force_delete` reads
`.git/refs/remotes/*` + `.git/packed-refs` (is the branch pushed?, so a recoverable
force-delete doesn't prompt), and `git_branch_off_protected` / `git_branch_off_stale_main`
read `.git/HEAD` + `refs/heads/*` + `refs/remotes/origin/*` (branch only off an
up-to-date main/master — see below). These are cheap file reads, not subprocesses and not
history. The line stays: no `git log`/`git rev-parse` subprocesses, no parsing history.
If a decision needs more than reading a few ref files, reconsider.

HEAD is read through `_head_file()`, which follows the **invocation's cwd** (filled in
`main()` from the payload's `cwd`, kept in `_INVOCATION`), not `PROJECT_DIR`: in a linked
worktree the session runs in `.claude/worktrees/<name>` while `CLAUDE_PROJECT_DIR` still
points at the main checkout, and HEAD is per-worktree (`.git` there is a *file* holding
`gitdir: <path>`). Shared refs (`refs/heads`, `refs/remotes`) live in the main `.git`, so
the ref readers keep using `PROJECT_DIR`.

**The one network call: `_branch_has_merged_pr` (`gh pr list --head <branch> --state merged`).**
It exists because no local file can answer "did this branch's PR already land": a squash
merge leaves the branch off trunk's ancestry, and GitHub keeps the remote ref after merging.
It runs only on `git commit` / bare `git push`, only off main/master, with a 10s timeout, and
**fails open** on every unclear answer (no `gh`, non-GitHub repo, unauthenticated, offline) —
an unanswerable question must never block a commit. Don't grow a second one: if a new rule
wants the network, the bar is the same — a decision that is impossible locally *and* worth a
round-trip on every matching command.

## The decision rule

Classify every new command (and every new flag combo) into one of three buckets:

- **`allow`** — safe. Read-only inspection, idempotent queries, anything that
  cannot damage state or leak information regardless of arguments. The agent
  should never have to ask the human about these. Examples: `ls`, `cat` (of
  non-`.env*` paths), `git status`, `git log`, `gcloud … list`, `find -name`.
  An allow rule may also carry a **reminder**: a non-empty `reason` on an
  `allow` rule is delivered to the agent as `additionalContext` (a soft nudge,
  no prompt) — see "allow + reminder" below. Use it for reversible-but-suspect
  actions where a hard `deny` would be the contradicting-voice bug, but silence
  would let an easy mistake through.

- **`ask`** — needs human audit, and is worth stalling the agent for. The
  command is **legitimate** but its effect leaves this working copy: an outward
  message, a machine-wide or remote mutation, code pulled off the network.
  Examples: `gh pr comment`, `gh issue create`, `npm install` (changes the
  dependency tree from the network), `systemctl restart` (affects running
  services), `curl -X POST` to remote, `gcloud … deploy`. If the effect stays
  inside the repo and can be undone, it's an `allow` — see below.

- **`deny`** — destructive, irreversible, or impossible-to-justify in any
  agent context. The reason is shown to the agent; it must redirect, not
  prompt the human. Examples: `git push --force`, `git reset --hard`,
  `git rebase`, `git push` to main/master (`git_push_to_protected_branch` —
  branch + PR instead), reading `.git/` with cat/head/tail/less/more/grep/rg
  (`any_path_under_git` — use `git` commands, `.git` is off-limits),
  `gh pr merge` (user-only), `git commit` / bare `git push` on a branch whose PR is
  already merged (`git_write_on_merged_branch` — that branch is finished; branch off
  updated trunk instead), `rm` outside the scratch dir (see below),
  `sudo`, `eval`, `bash <file>` (but `bash -c '<literal>'` is recursed — below).
  (`git merge` and `git cherry-pick` are **allow** — reversible local ops.
  Creating a branch off a non-trunk or stale base is **allow + reminder**, not
  deny — see below.)

**Every `ask` is a stall.** It stops the agent mid-task and makes a human read a
prompt, so `ask` has to earn its place: the effect must reach *outside this
working copy* (a comment on someone's PR, a deploy, a package pulled from the
network, a service restart on the machine) or be otherwise unrecoverable once
done. Local and reversible ⇒ `allow`, with a reminder if it's suspect. Never
works, or can't be justified for an agent ⇒ `deny`, with the way out spelled
out. "I'm not sure" is not a reason to prompt — decide, and put the doubt in
the reason text.

Applied examples: `git config <k> <v>`, `git revert`, `git init`, `git branch -D`
of an unpushed branch, `docker rm/rmi/compose` → **allow** (all reversible, all
local). `git clean -f`, `docker prune`, bare interactive `claude`,
`--dangerously-skip-permissions` → **deny** (unrecoverable, machine-wide, or
simply can't work under an agent). `gh pr comment`, `npm install`,
`curl -X POST <remote>`, `gcloud … deploy`, `systemctl restart` → **ask** (each
one reaches outside the repo).

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

## allow + reminder: nudge the agent without blocking

A fourth shape sits between `allow` and `ask`: **allow the command, but deliver
a reminder to the agent.** Set `decision: "allow"` with a non-empty `reason`;
the hook routes that reason into the PreToolUse `additionalContext`, which the
agent receives as a `<system-reminder>` before acting. No prompt fires, the user
isn't interrupted, and the command runs — it's a soft nudge, not a gate.

Mechanics (in `acl_hook.py`): `_emit` puts an allow `reason` into
`additionalContext` (not `permissionDecisionReason`, which on `allow` is
user-facing only and never reaches the agent — verified empirically). On `allow`
`permissionDecisionReason` stays empty, so the nudge never leaks to the user.
`_resolve_chained` carries the first allow-reminder through a command chain when
nothing stricter fires.

When to reach for it: a **reversible** action that's *probably* a mistake but
legitimately what the user might want. A hard `deny` there is the
contradicting-voice bug (see Waiting / polling) — it dead-ends a valid action;
silence lets an easy error through. The reminder threads the needle. Current
users: `git_branch_off_protected` and `git_branch_off_stale_main` — branching off
a non-trunk or stale base proceeds, but the agent is reminded to confirm intent /
pull first. Phrase the reason as a second-person nudge ("You're branching off
…"), not a redirect — the action already happened.

Don't overuse it: most commands are cleanly allow / ask / deny. A reminder on a
genuinely safe command is just noise in the agent's context.

## Where the ACL config lives — one file, inside the plugin

The rule table is **`plugins/acl-hook/hooks/acl.json`**, read straight from the
plugin dir on every invocation. There is no per-project copy, no install step, no
sync stamp: every project runs the rules of the installed plugin version, always.

**To change rules, edit `hooks/acl.json` and bump the plugin `version`.** There is
nowhere else to edit — a `.claude/acl.json` in a project is dead weight (an
artifact of the old install-and-sync scheme, safe to delete).

Per-project rule overrides are deliberately **not** supported. They existed, were
never used, and only created a way to run silently stale rules. If a rule needs to
differ per project, that's a signal the rule is project-specific and doesn't belong
in this plugin at all (see "Common mistakes").

## Logging: every decision, one file, `~/.claude/logs/acl-hook.log`

`LOG_PATH` in `acl_hook.py`. Two line shapes per Bash call: `decision=…
matched=<what fired>` for each sub-command, and one `final=…` for what the agent
actually got. That's the debugging entry point — when a rule "doesn't work",
grep the log before reading code. Rotated at 5 MB × 5 gzipped generations.

Keep it that way: any new gate or conversion logs a line with a `matched=` tag
naming itself (see `autonomous_ask_denied`), so the log alone explains the
verdict. A silent path is a path nobody can debug.

## Autonomous mode: `ACL_HOOK_AUTONOMOUS=1` turns every `ask` into `deny`

With nobody at the keyboard (`claude -p`, cron, CI) an `ask` is useless — it can't
be answered. Setting `ACL_HOOK_AUTONOMOUS=1` in the environment Claude Code runs in
converts each `ask` into a `deny` at emit time in `main()`, appending a short note to
the rule's own reason so the agent knows *why* and what to do instead (route around
it, or finish the rest and report the command for the user).

Keep the conversion where it is — one place, after `_decide` — so the rule table
stays a single source of truth and every rule's own actionable reason is preserved.
Don't add per-rule "autonomous" variants.

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

`rm` has exactly two outcomes — `allow` inside the scratch dir `.scratch/`,
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
5. **Run** `make ci` (or `uv run pytest plugins/` + `uv run ruff check plugins/`).

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
uv run pytest plugins/acl-hook/tests/ -q
uv run ruff check plugins/
uv run mypy plugins/acl-hook/hooks/acl_hook.py
```

Conftest pins `PROJECT_DIR` to a tmp dir with `app/`, `tests/`, `infrastructure/`,
`web/`, `tmp/` pre-created, so path-inside-project tests are deterministic.
If you add a path-based rule that depends on a different layout, extend the
fixture, don't hardcode `os.path` calls in tests.

## What this file is not

This is not a tutorial. It assumes you've read `README.md` and skimmed
`acl_hook.py`. If something here is unclear, the source is the authority.
