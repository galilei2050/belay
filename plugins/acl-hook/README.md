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
- **Genuinely ambiguous stuff still asks you.** `curl -X POST` to an unknown
  host, `npm install some-package`, `rm` inside the project. The prompt is now
  worth reading because it's the only one you get.

The net effect: fewer prompts, and every prompt matters.

## Install

```
/plugin marketplace add galilei2050/belay
/plugin install acl-hook@belay
```

Requirements: Python ≥ 3.10 available as `python3`, plus `pip install bashlex`
(used to parse compound commands properly — `a && b | c`, here-docs, command
substitutions are all decomposed into the individual commands they expand to,
so dangerous parts can't hide inside a pipeline).

After install, run once per project:

```
/acl-hook:init
```

This drops a starter `rules.yaml` into `.claude/acl-hook/rules.yaml` of the
current project, pre-populated with the default ruleset. Edit it to taste —
the hook reloads on every invocation, so changes apply immediately.

## How decisions are made

For each Bash call the hook receives, it:

1. Parses the command with `bashlex` and walks every sub-command (so
   `git status && rm -rf build` is two decisions, not one).
2. For each sub-command, looks up rules for that program (`git`, `rm`, `curl`,
   …). Rules match on argument patterns.
3. Returns the first matching action: `allow`, `ask`, `deny`. If nothing
   matches, falls back to the program's `default` (usually `ask`).
4. On `deny`, writes a one-line reason to stderr so you and the agent both see
   why.

## Default ruleset

Ships with sane defaults aimed at "experienced developer who wants to stop
clicking approve":

| Category | Default | Examples |
|---|---|---|
| Read-only inspection | `allow` | `ls`, `cat`, `head`, `tail`, `wc`, `file`, `stat`, `du`, `df`, `which`, `whereis`, `type`, `env`, `pwd` |
| Read-only git | `allow` | `git status`, `git diff`, `git log`, `git show`, `git branch` (no flags), `git remote -v` |
| Search | `allow` | `grep`, `rg`, `find` (without `-delete`/`-exec rm`) |
| Package manager queries | `allow` | `npm ls`, `pip list`, `pip show`, `cargo tree` |
| Process inspection | `allow` | `ps`, `top -n`, `lsof`, `netstat` |
| Mutating git | `ask` | `git commit`, `git checkout`, `git merge`, `git pull` |
| History rewrites | `deny` | `git rebase`, `git push --force`, `git reset --hard`, `git filter-branch`, `git reflog expire`, branch deletion of `main`/`master` |
| File deletion | `ask` inside project, `deny` outside | `rm`, `rmdir`; `rm -rf /` and similar always denied |
| Permission changes | `deny` for world-writable | `chmod 777`, `chmod -R 777` |
| Network mutation | `ask` | `curl -X POST/PUT/DELETE/PATCH`, `wget --post`, `nc` listeners |
| Network read | `allow` | `curl` GET, `wget` GET to plaintext URLs |
| Shell-into-pipe | `deny` | `curl … \| sh`, `wget -O- … \| bash`, `eval "$(…)"` |
| Inline code execution | `deny` | `python -c …`, `node -e …`, `ruby -e …`, heredoc'd shell |
| Package install | `ask` | `npm install`, `pip install`, `apt install`, `brew install`, `cargo install` |

The defaults are deliberately conservative on the deny side and liberal on
the allow side for read-only operations. Customize via `rules.yaml`.

## Configuring rules

`.claude/acl-hook/rules.yaml` (project) overrides `~/.claude/acl-hook/rules.yaml`
(user) overrides the plugin's bundled defaults. You only need to specify the
diffs — unspecified categories inherit.

Example: a project that wants to allow `make test*` targets without asking:

```yaml
make:
  rules:
    - match: { args_glob: ["test*"] }
      action: allow
```

Example: a project that uses a private container registry and wants `docker
push` to that host allowed:

```yaml
docker:
  rules:
    - match: { args: [push] , args_glob: ["registry.mycorp.internal/*"] }
      action: allow
```

Matcher keys available on each rule:

- `args: [a, b, c]` — these tokens appear in order (subsequence match)
- `args_contain: [a, b]` — any of these tokens appears
- `args_glob: ["pattern*"]` — any token matches the shell glob
- `predicate: name` — a Python predicate from `predicates.py` returns true
  (escape hatch for the rare rule that can't be expressed as patterns)

JSON Schema is shipped alongside as `rules.schema.json`. Drop this at the top
of your `rules.yaml` for IDE autocomplete and inline validation:

```yaml
# yaml-language-server: $schema=../../path/to/rules.schema.json
```

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

## Logs

Each decision is logged as a single JSON line to
`~/.claude/logs/acl-hook.log`. Useful when you want to know "why did it deny
that?" or "what did it auto-approve over the last hour?". Trim the file
yourself; the hook doesn't rotate.

## Exit behavior

- Exit 0 with empty stderr → allow silently.
- Exit 0 with stderr → allow, surface the message as a notice.
- Exit 2 → deny. Stderr is shown to the agent as the reason.
- Any other exit code → hook itself crashed; Claude Code falls back to
  asking the user. The crash is logged.

## License

Proprietary. See the repository [LICENSE](../../LICENSE).
