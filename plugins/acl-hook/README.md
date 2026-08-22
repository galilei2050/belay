# acl-hook

A PreToolUse Bash gate that **auto-approves obviously safe commands and
auto-denies obviously dangerous ones**, so Claude Code only stops to ask you
about the commands in the middle.

That's the whole job. It doesn't know about your project, your tests, your
review process, or your branch strategy. It looks at the command the agent
wants to run and decides one of three things: `allow`, `ask`, `deny`.

## Why you might want it

Out of the box Claude Code asks for permission on almost every Bash call. The
prompt fatigue trains you to click "approve" without reading — which is the
exact moment something dangerous slips through. acl-hook flips this:

- **Boring stuff runs without asking.** `ls`, `git status`, `git diff`, `cat`,
  `grep`, `pwd`, `which`, `find -name`, read-only `jq`, etc.
- **Dangerous stuff is denied outright, with a reason.** `rm -rf /`,
  `git push --force` on protected branches, `curl … | sh`, here-doc'd shell
  pipelines, `chmod 777`, etc. You see the denial; the agent has to ask you
  or rephrase.
- **Only outward-facing stuff still asks you.** `curl -X POST` to an unknown
  host, `npm install some-package`, `gh pr comment`, a deploy. The prompt is now
  worth reading because it's the only one you get.

The net effect: fewer prompts, and every prompt matters.

## Install

```
/plugin marketplace add galilei2050/belay
/plugin install acl-hook@belay
```

Requirements: Python ≥ 3.10 available as `python3`, an authenticated GitHub CLI
(`gh`) for the merged-branch check — without it that one rule silently no-ops,
everything else works — plus `pip install bashlex`
(used to parse compound commands properly — `a && b | c`, here-docs, command
substitutions are all decomposed into the individual commands they expand to,
so dangerous parts can't hide inside a pipeline).

Linux only, in practice: the `timeout` wrap around unbounded commands is GNU
coreutils ≥ 8.29 (for `-v`). macOS ships no `timeout(1)` at all and BusyBox's
has no `-v`, so on those hosts every rewritten command would fail to launch.

There is no per-project setup step. The rule table ships inside the plugin and
is read from there on every invocation, so every project runs the rules of the
installed plugin version.

## How decisions are made

For each Bash call the hook receives, it:

1. Parses the command with `bashlex` and walks every sub-command (so
   `git status && rm -rf build` is two decisions, not one).
2. For each sub-command, looks up rules for that program (`git`, `rm`, `curl`,
   …). Rules match on argument patterns.
3. Returns the first matching action: `allow`, `ask`, `deny`. If nothing
   matches, falls back to the program's `default`.
4. On `deny` / `ask`, the reason is handed to the agent (and, for `ask`, shown
   to you in the prompt), so both sides see why.

## The ruleset

Aimed at "experienced developer who wants to stop clicking approve":

| Bucket | Examples |
|---|---|
| `allow` — read-only inspection | `ls`, `cat` (not `.env*`), `grep`, `rg`, `find`, `ps`, `git status/diff/log/branch`, `npm ls`, `pip show` |
| `allow` — reversible local work | `git add <paths>`, `git commit`, `git merge`, `git revert`, `git config <k> <v>`, `git pull`, `git push <feature-branch>`, `git branch -D`, `docker build/rm/compose`, `make`, `rm` inside `.scratch/` |
| `ask` — legitimate, but the effect leaves your working copy | `npm install`, `pip install`, `curl -X POST` to a remote host, `systemctl restart`, `gh pr comment`, `gh issue create`, `gcloud … deploy`, `docker push` |
| `deny` — destructive, or can't work under an agent | `git push --force`, `git push` to `main`/`master`, `git commit` on a branch whose PR already merged, `git reset`, `git rebase`, `git checkout <path>` / `git restore <path>`, `git stash drop`/`clear`, `git add -A`, `git clean -f`, `gh pr merge`, `docker prune`, `sudo`, `eval`, `bash file.sh`, `cat .env`, reading `.git/`, `rm` outside `.scratch/`, `>` / `tee` writing outside the project or into `.git/`, heredocs, bare interactive `claude` |

Every `ask` stalls the agent and costs you a prompt, so the bar for one is high:
the command has to reach outside this working copy. Anything local and
reversible is allowed outright — sometimes with a reminder delivered to the
agent instead of a prompt to you.

One class is worth naming on its own: **the commands that overwrite a dirty
working tree**. `git reset`, `git clean -f`, `git checkout <path>`,
`git restore <path>` and `git stash drop` all destroy work that was never
committed — no reflog entry, no dangling object, nothing to recover from — and
the tree they revert holds your edits alongside the agent's. Across published
incident reports this is the most common way a coding agent destroys work, so
all of them are denied. What still works is the half that cannot lose anything:
`git checkout <branch>`, `git checkout -b`, `git restore --staged` (unstage,
file untouched), and reading the stash with `git stash list`.

The authority is `hooks/acl.json` — read it when you want the exact answer for
a command; the table above is a summary, not a spec.

## Configuring rules

You don't. The rule table lives in the plugin (`hooks/acl.json`) and is read
from there — there's no per-project config file to install, edit, or keep in
sync, which means a project can never silently run a stale ruleset. Change the
rules by editing that file and bumping the plugin version (see
`CLAUDE.md` in this directory).

Matcher kinds available on each rule:

- `args: [a, b, c]` — these tokens appear in order (subsequence match)
- `args_contain: [a, b]` — any of these tokens appears
- `args_glob: "pattern*"` — the full argument string matches the glob
- `fn: name` — a Python predicate in `acl_hook.py` returns true (escape hatch
  for the rare rule that can't be expressed as patterns)

## Autonomous mode

When Claude Code runs with nobody at the keyboard (`claude -p`, cron, CI), an
`ask` is useless — there is no one to answer it. Set:

```
ACL_HOOK_AUTONOMOUS=1
```

in the environment and every `ask` becomes a `deny`, with the rule's own reason
plus a note telling the agent to route around it or report the command for you
to run yourself. `allow` and `deny` are unaffected. Accepted values: `1`,
`true`, `yes` (anything else, including unset, keeps normal behavior).

## What this plugin is NOT

To keep the scope honest:

- **Not a verification gate.** It doesn't care whether your tests pass before
  a commit. If you want that, use a separate plugin (e.g. a future
  `verify-gate` in this marketplace).
- **Not a plan/scope enforcer.** It doesn't read your plan and block edits
  outside it. Different plugin.
- **Not a code-review gate.** It doesn't know what "reviewed" means.
- **Not a secret scanner.** It won't stop `echo $API_KEY`.
- **Not project-aware.** No hardcoded domain allowlists, no hardcoded test
  commands, no knowledge of your branch naming. Everything project-specific
  lives in your `rules.yaml`.

If you want any of the above, compose acl-hook with another plugin. That's
the whole point of belay being a marketplace and not a monolith.

## Logs — `~/.claude/logs/acl-hook.log`

Every decision is logged there, one line each, for all projects on the machine.
Rotated at 5 MB with 5 gzipped generations (`acl-hook.log.1.gz`, …).

```
[2026-07-28 19:38:59] received command="git status && git push --force" agent=main
[2026-07-28 19:38:59] decision=allow command="git status" matched=rule agent=main
[2026-07-28 19:38:59] decision=deny command="git push --force" matched=rule agent=main
[2026-07-28 19:38:59] final=deny command="git status && git push --force" agent=main
```

Every command produces a `received` line before any work and exactly one
`final=` line after:

- `received` — the full command as the hook got it (newlines escaped, never
  truncated). Written first, so even a command that crashes the hook leaves a
  trace.
- `decision=` — one line per sub-command (`a && b` produces two), with
  `matched=` naming what fired: a `rule`, a `default:<x>`, or a gate
  (`agent_heredoc`, `command_too_long`, `autonomous_ask_denied`, …).
- `final=` — the verdict the agent actually got, after the strictest-wins merge
  across sub-commands. Variants: `final=rewrite` (a detached command was
  silently wrapped in `timeout` — `matched=background_unbounded`), `final=skip`
  (not a Bash call), `final=error`
  (the hook itself crashed — the traceback follows, and Claude Code falls back
  to prompting).

Answering "why was that denied?":

```
grep 'final=deny' ~/.claude/logs/acl-hook.log | tail -20
grep 'git push' ~/.claude/logs/acl-hook.log
```

If the log has no recent lines at all, the hook isn't running — check that
`acl-hook@belay` is `true` under `enabledPlugins` in `~/.claude/settings.json`
(and restart Claude Code after changing it).

## Output contract

The hook writes a single JSON object on stdout: a `PreToolUse`
`hookSpecificOutput` carrying `permissionDecision` (`allow` / `ask` / `deny`)
and `permissionDecisionReason`. An `allow` rule with a reason delivers it as
`additionalContext` (a nudge to the agent, no prompt); a detached command
(`run_in_background: true`) comes back as `allow` plus an `updatedInput` that
wraps it in `timeout`. A poll loop that carries no `timeout` of its own is
denied, with the three ways out in the reason.

## License

[AGPL-3.0](../../LICENSE).
