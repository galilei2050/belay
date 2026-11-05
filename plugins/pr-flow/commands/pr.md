---
description: Open or refresh the PR for the current branch, with a description a reviewer can act on
---

Take the current branch all the way to a proposed change, without waiting for the hook to ask.

1. Push if anything is local-only (`git push -u origin HEAD` on a branch with no upstream).
2. `gh pr view --json number,url,state,body` — decide whether you are opening or refreshing.
3. Write the body with the `pr-flow:pr-description` skill. Collect the facts first (the failure
   and its measured size, what you verified with real outcomes, what stayed out of scope), then
   diagram what prose explains badly.
4. `gh pr create --draft --title "<outcome claim>" --body-file <file>` or `gh pr edit <number> --body-file
   <file>`. Never pass the body inline — the ``` fences are backticks, and a double-quoted shell
   argument runs them as command substitution. The gate judges the title; take its rewrite if it
   denies one. A new PR is always a draft: a draft runs no CI, so in-progress pushes cost nothing.
5. When the code is done and local checks are green, `gh pr ready <number>` — that starts the CI run.

If `$ARGUMENTS` names a PR number or a branch, work on that one instead of the current branch.

Report the PR URL and, in one line, what the body now claims that it did not before.
