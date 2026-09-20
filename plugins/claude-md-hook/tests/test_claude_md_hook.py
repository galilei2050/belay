"""Tests for plugins/claude-md-hook/hooks/claude_md_hook.py, driven through the script itself."""

from datetime import datetime

TODAY = datetime.now().astimezone().date().isoformat()

GOOD = """# CLAUDE.md — demo

Updated: 2020-01-01

## Business decisions

None at this level.

## Technical decisions

- **Stdlib only** — hooks run on bare python3. Rejected: click, a dependency for one flag.
"""


def write(path, text):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


# ── the hook stays out of everything that is not a CLAUDE.md ─────────────────


def test_other_files_are_ignored_and_untouched(repo, after_write):
    readme = write(repo / "README.md", "# demo\n")
    assert after_write(readme) is None
    assert readme.read_text() == "# demo\n"


# ── stamping ─────────────────────────────────────────────────────────────────


def test_a_clean_file_is_silent_and_gets_todays_date(repo, after_write):
    target = write(repo / "CLAUDE.md", GOOD)
    assert after_write(target) is None
    assert f"Updated: {TODAY}" in target.read_text()
    assert "2020-01-01" not in target.read_text()


def test_a_missing_date_line_is_inserted_under_the_title(repo, after_write):
    target = write(repo / "CLAUDE.md", GOOD.replace("Updated: 2020-01-01\n\n", ""))
    assert after_write(target, "Edit") is None
    assert target.read_text().splitlines()[:3] == ["# CLAUDE.md — demo", "", f"Updated: {TODAY}"]


def test_a_file_already_stamped_today_is_not_rewritten(repo, after_write):
    target = write(repo / "CLAUDE.md", GOOD.replace("2020-01-01", TODAY))
    before = target.stat().st_mtime_ns
    assert after_write(target) is None
    assert target.stat().st_mtime_ns == before


# ── findings come back as a block ────────────────────────────────────────────


def test_missing_sections_block_with_each_one_named(repo, after_write):
    out = after_write(write(repo / "CLAUDE.md", "# CLAUDE.md — demo\n\nRun `make ci`.\n"))
    assert out["decision"] == "block"
    assert "missing section `## Business decisions`" in out["reason"]
    assert "missing section `## Technical decisions`" in out["reason"]


def test_an_empty_section_blocks_but_an_explicit_none_does_not(repo, after_write):
    empty = GOOD.replace("None at this level.\n", "")
    out = after_write(write(repo / "CLAUDE.md", empty))
    assert "`## Business decisions` is empty" in out["reason"]
    assert "Technical decisions" not in out["reason"]


def test_a_heading_inside_a_code_fence_is_not_a_section(repo, after_write):
    fenced = GOOD.replace("## Business decisions\n\nNone at this level.\n", "```\n## Business decisions\nx\n```\n")
    out = after_write(write(repo / "CLAUDE.md", fenced))
    assert "missing section `## Business decisions`" in out["reason"]


def test_over_200_lines_blocks(repo, after_write):
    out = after_write(write(repo / "CLAUDE.md", GOOD + "\n## Notes\n" + "- filler line\n" * 200))
    assert "limit is 200" in out["reason"]


def test_a_dead_path_in_a_real_tree_blocks_while_lookalikes_pass(repo, after_write):
    text = GOOD + "\nSee `src/app.py`, `src/gone.py`, `origin/main`, `plugins/<name>/x.py`.\n"
    out = after_write(write(repo / "CLAUDE.md", text))
    assert "`src/gone.py`" in out["reason"]
    for fine in ("src/app.py", "origin/main", "<name>"):
        assert fine not in out["reason"]


def test_a_decision_repeated_from_a_parent_blocks(repo, after_write):
    write(repo / "CLAUDE.md", GOOD)
    out = after_write(write(repo / "src" / "CLAUDE.md", GOOD))
    assert "decision already recorded in" in out["reason"]
    assert "stdlib only" in out["reason"]


def test_a_child_with_its_own_decisions_is_clean(repo, after_write):
    write(repo / "CLAUDE.md", GOOD)
    child = GOOD.replace("Stdlib only", "One module per tool")
    assert after_write(write(repo / "src" / "CLAUDE.md", child)) is None


# ── audit CLI ────────────────────────────────────────────────────────────────


def test_audit_of_a_dir_covers_tracked_files_only(repo, git, run_audit):
    write(repo / "CLAUDE.md", GOOD)
    git(repo, "add", "CLAUDE.md")
    git(repo, "commit", "-qm", "docs")
    write(repo / "untracked" / "CLAUDE.md", "# nothing\n")
    assert run_audit(repo) == (0, "")


def test_audit_reports_findings_and_fails(repo, run_audit):
    bad = write(repo / "CLAUDE.md", "# CLAUDE.md — demo\n")
    status, out = run_audit(bad)
    assert status == 1
    assert "no `Updated: YYYY-MM-DD` line" in out


def test_audit_flags_a_file_the_code_has_left_behind(repo, git, run_audit):
    target = write(repo / "CLAUDE.md", GOOD)
    git(repo, "add", "CLAUDE.md")
    git(repo, "commit", "-qm", "docs")
    for n in range(19):
        write(repo / "src" / f"m{n}.py", "x = 1\n")
        git(repo, "add", ".")
        git(repo, "commit", "-qm", f"m{n}")
    assert run_audit(target) == (0, "")  # 19 commits: under the threshold
    write(repo / "src" / "last.py", "x = 1\n")
    git(repo, "add", ".")
    git(repo, "commit", "-qm", "last")
    status, out = run_audit(target)
    assert status == 1
    assert "stale: 20 commits" in out
