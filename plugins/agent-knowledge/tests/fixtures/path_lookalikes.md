# CLAUDE.md — demo

Updated: 2020-01-01

## Business decisions

None at this level.

## Technical decisions

Each span below starts in the real `src/` directory, so only the not-a-path filter spares it:

- a placeholder: `src/<name>/x.py`
- a glob: `src/*.py`
- a file:line reference: `src/app.py:12`
- a brace set: `src/{a,b}.py`

These never anchor on a real directory at all:

- an env var: `$HOME/src/x`
- a git ref: `origin/main`
- a dot-relative path into nowhere: `./nope/x.py`
- another user's home: `~nosuchuser/x.py`
