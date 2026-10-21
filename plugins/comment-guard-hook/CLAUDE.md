# CLAUDE.md — comment-guard-hook

Updated: 2026-10-20

Internal policy for this plugin. The rule itself and its rationale are in `README.md`; the soft
half lives in `rules/comments-why-not-what.md`.

## The replay, and where it diverges from the real tool

`PreToolUse` is handed the *request*, not the result, so `post_edit_source` reconstructs what the
file will hold by replaying the edit over the copy on disk. `PostToolUse` would get the true
after-image for free and cannot deny, which is the whole point of the gate — so the replay is the
price of blocking before the prose lands.

One known divergence, accepted: `str.replace` with an `old_string` that is not in the file is a
no-op, so the hook sees `after == before`, finds nothing, and stays silent — where the real `Edit`
would have errored. Nothing lands either way, so the silence costs nothing. Do **not** "fix" this
by erroring: the hook would then be reporting the edit tool's failures in the edit tool's place.

If a sibling hook ever needs the same reconstruction, lift `replay` / `_changed_lines` /
`judged_lines` into a new edit-replay module beside the hook and let it copy one file. Plugins here are self-contained
by policy (`docs/AUTHORING.md`) — vendor it, don't import across plugins.

## Why the tool list is written down twice

`EDIT_TOOLS` in the hook and the `matcher` in `hooks.json` must name the same set, and
`test_every_tool_in_the_matcher_is_handled` fails if they drift. A tool the matcher admits but the
hook does not recognise would be replayed as an `Edit` and die on a missing `old_string`;
`NotebookEdit` is the near miss, since it spells its target `notebook_path`.

## Adding an exemption

Exemptions are mechanical or they do not go in. Blank lines, trailing comments and `_DIRECTIVE`
headers qualify because each is recognisable from syntax alone. An exemption that needs to know
what a comment *says* belongs in `review-panel:comments-reviewer`, not here — that judgement is
exactly what this plugin exists because a hook cannot make.

## Business decisions

- **Docstrings and `#` blocks cap at 3 non-blank lines; module docstrings exempt** — three lines hold the contract (how to call it, what comes back). Set by the owner.
- **No check on the word "legacy"** — the owner rejected it.

## Technical decisions

- **A new deny must be mechanical, like an exemption** — `_LINE_REF` (`\w+\.py:\d+`) went in because it needs zero judgement: 22 hits, zero false positives over 5091 docstrings in clarity-auto-care. Rejected: content rules that need meaning, which belong to `review-panel:comments-reviewer`.
- **Line references are scanned in raw source lines, not the decoded docstring** — escapes like a backslash-newline shift decoded line numbers. Rejected: offsets into `ast.get_docstring` text.
- **Ordinary string literals are not scanned for line references** — the tests carry offending text in string fixtures, and a literal-wide scan would deny editing them.
