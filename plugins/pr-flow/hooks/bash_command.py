#!/usr/bin/env python3
"""Which parts of a Bash command string actually run, for the hooks that judge one before or after.

Quoted text is prose — an `echo`, a commit message — and must not read as a call. The exception is
the argument of `sh -c` / `eval`: that quoted span *is* the command, and skipping it would make
`bash -lc '…'` the easiest way past every gate here.
"""

from __future__ import annotations

import re

_QUOTED_RE = re.compile(r"'[^']*'|\"[^\"]*\"")
# Flags may sit before the `-c` or be fused to it: `bash -lc '…'` is the commoner idiom.
_SHELL_C_RE = re.compile(
    r"(?:ba|z|da|fi)?sh\s+(?:-\S+\s+)*-[a-zA-Z]*c\s+(['\"])(.*?)\1|eval\s+(['\"])(.*?)\3", re.DOTALL
)


def blank_quotes(command: str) -> str:
    """`command` with every quoted span replaced by spaces of the same length, so offsets still align."""
    return _QUOTED_RE.sub(lambda found: " " * len(found.group()), command)


def shell_bodies(command: str) -> list[str]:
    """The command strings handed to `sh -c` / `eval` inside `command`."""
    return [found.group(2) or found.group(4) or "" for found in _SHELL_C_RE.finditer(command)]
