# acl-hook

PreToolUse hook that gates every `Bash` tool call against a pattern-based access
control list. For each command, the rules can `allow`, `ask` (prompt the user),
or `deny`. Built on a `bashlex` AST walk so chained commands, substitutions, and
heredocs are seen as the actual commands they expand to.

## Install

```
/plugin marketplace add galilei2050/belay
/plugin install acl-hook@belay
```

Requires Python ≥ 3.10 on `$PATH` as `python3`, and the `bashlex` package:

```
pip install bashlex
```

## Wiring

```json
{
  "hooks": {
    "PreToolUse": [
      {
        "matcher": "Bash",
        "hooks": [
          { "type": "command", "command": "python3 ${CLAUDE_PLUGIN_ROOT}/hooks/acl_hook.py" }
        ]
      }
    ]
  }
}
```

## Status: verbatim extraction

This is a **lift-and-shift** from the clarity-auto-care project. It works in
that project's layout and will likely need adjustment in others. Known coupling
to remove in a later revision:

- **ACL ruleset is hardcoded** in `acl_hook.py` (the `ACL = {...}` dict, ~430
  lines). To customize: edit the dict directly. A future revision will move
  this to an external YAML the user supplies.
- **`CODE_PREFIXES = ("app/", "web/", "tests/")`** assumes a specific monorepo
  layout — used to decide whether a commit needs verification artifacts.
- **`make lint test-app test-web`** is invoked during verification gates —
  assumes a Makefile with these targets.
- **Session-aware gates** (`code_review_not_passed`, `verification_not_passed`)
  read `.sessions/{id}.json`, `.work/{branch}/`, `.plan/` — they assume the
  rest of the clarity-auto-care harness is installed. Without it these gates
  fail open or fail closed depending on the rule.
- **`API_FETCH_DOMAINS`** is a hardcoded allowlist of API domains for `curl`.
- **Logs** to `~/Logs/claude_acl.log` (hardcoded path).
- **`PROJECT_DIR`** is computed as `__file__/../..` — verify the resolved path
  is what you want when loaded from `${CLAUDE_PLUGIN_ROOT}/hooks/`.

## What it actually blocks

A non-exhaustive flavor of the rules baked in:

- `git rebase` (any form), `git push --force*`, `git reset --hard`, branch
  deletion, force-checkout
- `rm -rf` against anything outside well-known scratch paths
- `python -c '…'` one-liners, inline function definitions, heredocs that pipe
  to shells, chained `sleep` loops
- `curl` outside the configured API domain allowlist
- Commits when verification or code-review artifacts are missing for the
  current branch's session
- Movement of `.plan/` files (the plan directory is immutable once written)

## License

Proprietary. See the repository [LICENSE](../../LICENSE).
