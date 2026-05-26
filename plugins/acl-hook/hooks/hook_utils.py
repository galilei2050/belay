#!/usr/bin/env python3
"""Shared utilities for Claude Code hooks."""

import gzip
import json
import logging
import os
import re
import subprocess
import sys
import time
from logging.handlers import RotatingFileHandler
from pathlib import Path

from git import Repo

HOME = str(Path.home())
PROJECT_DIR = Path(os.path.realpath(os.path.join(os.path.dirname(__file__), "..", "..")))


def setup_logging(name: str) -> logging.Logger:
    """Create a rotating gzip logger writing to ~/Logs/claude_{name}.log."""
    log_dir = os.path.join(HOME, "Logs")
    os.makedirs(log_dir, exist_ok=True)
    logger = logging.getLogger(name)
    logger.setLevel(logging.INFO)

    def _gz_namer(n):
        return n + ".gz"

    def _gz_rotator(source, dest):
        with open(source, "rb") as f_in:
            with gzip.open(dest, "wb") as f_out:
                f_out.write(f_in.read())
        os.remove(source)

    handler = RotatingFileHandler(
        os.path.join(log_dir, f"claude_{name}.log"),
        maxBytes=5_000_000,
        backupCount=5,
    )
    handler.namer = _gz_namer
    handler.rotator = _gz_rotator
    handler.setFormatter(logging.Formatter("[%(asctime)s] %(message)s", datefmt="%Y-%m-%d %H:%M:%S"))
    logger.addHandler(handler)
    return logger


def load_session(session_id: str) -> dict:
    """Read .sessions/{session_id}.json and return parsed dict. Empty dict if file missing."""
    session_file = PROJECT_DIR / ".sessions" / f"{session_id}.json"
    if not session_file.exists():
        return {}
    return json.loads(session_file.read_text())


def find_work_dir(*, session_id: str) -> Path | None:
    """Return .work/{project_name}/ from .sessions/{session_id}.json, or None if no work dir exists."""
    session_data = load_session(session_id)
    return _work_dir_from_session(session_data)


def _work_dir_from_session(session_data: dict) -> Path | None:
    """Return .work/{branch}/ from parsed session dict, or None."""
    branch = session_data.get("branch")
    if not branch:
        return None
    work_dir = PROJECT_DIR / ".work" / branch
    if work_dir.is_dir():
        return work_dir
    return None


def get_artifact_path(*, session_id: str, artifact: str) -> Path | None:
    """Return absolute artifact path from session JSON, or None if artifact not mapped."""
    session_data = load_session(session_id)
    artifacts = session_data.get("artifacts", {})
    rel_path = artifacts.get(artifact)
    if not rel_path:
        return None
    return PROJECT_DIR / rel_path


def check_verdict(filepath: Path, *, expected: str = "PASSED") -> tuple[bool, str]:
    """Read file and check first line starts with VERDICT: {expected}. Returns (matched, reason)."""
    if not filepath.exists():
        return False, f"{filepath.name} does not exist"
    try:
        first_line = filepath.read_text().splitlines()[0]
    except (IndexError, OSError):
        return False, f"{filepath.name} is empty or unreadable"
    if first_line.startswith(f"VERDICT: {expected}"):
        return True, "ok"
    return False, f"{filepath.name} first line is '{first_line}', expected 'VERDICT: {expected}'"


def deny(reason: str) -> None:
    """Output PreToolUse deny JSON and exit."""
    print(json.dumps({
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "deny",
            "permissionDecisionReason": reason,
        }
    }))
    sys.exit(0)


def block(reason: str) -> None:
    """Output SubagentStop block JSON and exit."""
    print(json.dumps({"decision": "block", "reason": reason}))
    sys.exit(0)


CODE_PREFIXES = ("app/", "web/", "tests/")


def staged_has_code_changes(staged_files: list[str]) -> bool:
    """True if any staged file is under app/, web/, or tests/."""
    return any(f.startswith(CODE_PREFIXES) for f in staged_files)


def branch_has_code_changes() -> bool:
    """True if branch diff vs master includes app/, web/, or tests/ files."""
    repo = Repo(str(PROJECT_DIR), search_parent_directories=True)
    master = repo.commit("master")
    head = repo.head.commit
    diff = master.diff(head)
    files = [(d.b_path or d.a_path or "") for d in diff]
    return any(f.startswith(CODE_PREFIXES) for f in files)


# 15-min window matches typical subagent duration — tuning parameter, not a business rule.
DENY_WINDOW_SECONDS = 900
_LOG_TIMESTAMP_RE = re.compile(r"^\[(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})\]")


def find_recent_denies(*, agent_type: str, window_secs: int = DENY_WINDOW_SECONDS) -> list[str]:
    """Return deny log lines matching the given agent within the time window.

    Greps ~/Logs/claude_source_guard.log and ~/Logs/claude_acl.log (bounded tail
    of last 500 lines each to avoid loading the full rotating log). Used by
    progress_guard to verify that a subagent's BLOCKED claim corresponds to a
    real hook deny — if not, the subagent confabulated and its stop is rejected.
    """
    log_dir = Path.home() / "Logs"
    cutoff = time.time() - window_secs
    matches: list[str] = []
    for log_name in ("claude_source_guard.log", "claude_acl.log"):
        path = log_dir / log_name
        if not path.exists():
            continue
        tail = path.read_text().splitlines()[-500:]
        for line in tail:
            if "decision=deny" not in line:
                continue
            if f"agent={agent_type}" not in line:
                continue
            m = _LOG_TIMESTAMP_RE.match(line)
            if not m:
                continue
            ts = time.mktime(time.strptime(m.group(1), "%Y-%m-%d %H:%M:%S"))
            if ts >= cutoff:
                matches.append(line)
    return matches
