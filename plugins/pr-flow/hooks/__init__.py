"""Marks this directory as a package so the two entry-point scripts can share modules.

`nudge_after_git.py` and `require_pr.py` are what Claude Code executes; `branch_state.py` and
`nudges.py` are imported by both. They resolve through `sys.path[0]` (the script's own
directory) at runtime — this file exists so the linters read them as modules rather than as
stray scripts.
"""
