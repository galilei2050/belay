# branch-guard-hook

A `PreToolUse` gate that refuses `Write` / `Edit` while a **protected branch** (`main`,
`master`) is checked out. The branch-aware sibling of
[`fs-acl-hook`](../fs-acl-hook), which decides by path alone.

## What it does

[`acl-hook`](../acl-hook) already refuses `git push` to main/master — but that fires at the
end, once a pile of commits already sits on trunk and untangling them costs a branch and a
cherry-pick. This hook fires on the **first edit**, when `git checkout -b <feature>` still
fixes everything by carrying the uncommitted change across.

| Situation | Decision |
|-----------|----------|
| `Write`/`Edit` in the project, on `main` or `master` | **deny** — branch first |
| same edit on any other branch | *silent* |
| `.scratch/` (throwaways) or `.claude/` (harness config) on trunk | *silent* |
| path outside the project | *silent* — that's fs-acl-hook's boundary |
| detached HEAD, or not a checkout at all | *silent* — no branch, no branch policy |
| `Read` | *silent* — reading trunk is fine |

Only `deny` is ever emitted; every other case emits nothing, so this hook never overrides a
sibling hook's `allow`/`ask` on the same call.

The branch is read from the `.git/HEAD` nearest the **invocation's cwd**, not from
`CLAUDE_PROJECT_DIR` — inside a linked worktree the latter still names the main checkout and
would report trunk's branch for a worktree that is on a feature branch.

## Install

```
/plugin install branch-guard-hook@belay
```

## Config

None. Protected branches are `main` and `master`; the exempt dirs are `.scratch/` and
`.claude/`. A project that legitimately works on trunk should not install this plugin —
that's the opt-in, rather than a config key (see [PHILOSOPHY](../../docs/PHILOSOPHY.md):
composition over configuration).
