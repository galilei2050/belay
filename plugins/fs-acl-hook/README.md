# fs-acl-hook

A `PreToolUse` gate for the **file tools** (`Write` / `Edit` / `Read`) — the path-based
sibling of [`acl-hook`](../acl-hook), which gates `Bash` by command. It decides
`allow` / `ask` / `deny` from the path a file-tool call touches, so the agent stops
hitting permission prompts for its own scratch work and stops writing files where they
don't belong.

## What it does

For every `Write` / `Edit` / `Read` (first match wins):

| Path | Decision | Why |
|------|----------|-----|
| anything inside `.git/` | **deny** (read *and* write) | git's internal state — inspect it with `git` commands, not file reads |
| write under `.scratch/` | **allow** (no prompt) | the sanctioned scratch zone (acl-hook creates & gitignores it) |
| write outside the project | **deny** | no `/tmp` scatter, no `Edit(../other-repo/…)` — put throwaways in `.scratch/`, or cd into that repo |
| read outside the project | **ask** | confirm a one-off cross-repo read (e.g. a sibling library), or connect the dir |
| anything else (in-project) | *defer* | normal permission flow (your acceptEdits / review choice) |

The commit-message pattern it nudges: `Write .scratch/COMMIT_MSG` → `git commit -F .scratch/COMMIT_MSG`
(no Write prompt, and the leftover `rm` is allowed in `.scratch/`).

## Install

```
/plugin install fs-acl-hook@belay
```

## Config

None. The boundary is `$CLAUDE_PROJECT_DIR` (falls back to cwd), and the scratch dir is
`.scratch/` — both resolved per invocation, so it works in every repo with zero setup.
