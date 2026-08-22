---
description: Open or refresh the PR for the current branch, with a description a reviewer can act on
---

Take the current branch all the way to a proposed change, without waiting for the hook to ask.

1. Push if anything is local-only (`git push -u origin HEAD` on a branch with no upstream).
2. `gh pr view --json number,url,state,body` — decide whether you are opening or refreshing.
3. Write the body with the `pr-flow:pr-description` skill. Collect the facts first (the failure
   and its measured size, what you verified with real outcomes, what stayed out of scope), then
   diagram what prose explains badly.
4. `gh pr create --body-file <file>` or `gh pr edit <number> --body-file <file>`. Never pass a
   body inline — mermaid fences do not survive an argument list.

If `$ARGUMENTS` names a PR number or a branch, work on that one instead of the current branch.

Report the PR URL and, in one line, what the body now claims that it did not before.
