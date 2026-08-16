# branch-guard-hook

A `PreToolUse` gate that refuses file edits while a **protected branch** (`main`, `master`)
is checked out. The branch-aware sibling of [`fs-acl-hook`](../fs-acl-hook), which decides by
path alone.

## What it does

[`acl-hook`](../acl-hook) already refuses `git push` to main/master — but that fires at the
end, once a pile of commits already sits on trunk and untangling them costs a branch and a
cherry-pick. This hook fires on the **first edit**, when `git checkout -b <feature>` still
fixes everything by carrying the uncommitted change across.

Covers every tool in the matcher — `Write`, `Edit`, `MultiEdit`, `NotebookEdit`.

| Situation | Decision |
|-----------|----------|
| an edit in the project, whose checkout is on `main` or `master` | **deny** — branch first |
| the same edit on any other branch | *silent* |
| `.scratch/` — the sanctioned throwaway zone | *silent* |
| path outside the project | *silent* — that's fs-acl-hook's boundary |
| detached HEAD, or not a checkout at all | *silent* — no branch, no branch policy |
| `Read` | *silent* — reading trunk is fine (and not in the matcher) |

`<project>/.claude` is **not** exempt: it holds tracked source (skills, agents) and, under
`.claude/worktrees/`, whole checkouts. fs-acl-hook draws the same line — its carve-out is the
agent's home `~/.claude`, never a project's own.

Only `deny` is ever emitted; every other case emits nothing, so this hook never overrides a
sibling hook's `allow`/`ask` on the same call.

## Which checkout decides

The branch is read from the `.git/HEAD` nearest **the file being edited**, not the session's
cwd and not `CLAUDE_PROJECT_DIR`. HEAD is per-checkout state, and a session that entered a
worktree still reaches back into the main checkout: anchoring anywhere but the file lets an
edit to trunk through whenever the two disagree.

A git dir whose HEAD can't be read raises instead of returning "no branch" — for a hook whose
only job is to say "no", *I can't tell* has to be loud rather than silently off.

## Install

```
/plugin install branch-guard-hook@belay
```

## Config

None. Protected branches are `main` and `master`; the only exempt dir is `.scratch/`. A project
that legitimately works on trunk declines by not installing the plugin — that's the opt-in,
rather than a config key (see [PHILOSOPHY](../../docs/PHILOSOPHY.md): composition over
configuration).
