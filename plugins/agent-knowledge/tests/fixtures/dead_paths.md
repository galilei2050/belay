# CLAUDE.md — demo

Updated: 2020-01-01

## Business decisions

None at this level.

## Technical decisions

The test repo holds one real file, src/app.py, and nothing else under src.

- **Entry point is `src/app.py`** — exists, so it is fine.
- **Helpers live in `src/gone.py`** — the directory is real, the file is not.
- **Workers live in `src/sub/gone.py`** — the first segment is real, the middle one is not.
