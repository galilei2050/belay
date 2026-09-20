"""Tests for plugins/claude-md-hook/hooks/claude_md_hook.py, driven through the script itself."""

from datetime import datetime

TODAY = datetime.now().astimezone().date().isoformat()

GOOD = """# CLAUDE.md — demo

Updated: 2020-01-01

## Business decisions

None at this level.

## Technical decisions

- **Stdlib only** — hooks run on bare python3.
  Rejected: click, a dependency for one flag.
"""


def padded(total_lines):
    """GOOD grown to exactly `total_lines`; it already carries its `Updated:` line, so stamping adds none."""
    return GOOD + "- filler\n" * (total_lines - len(GOOD.splitlines()))


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


def test_the_date_goes_under_a_title_that_is_not_the_first_line(repo, after_write):
    text = GOOD.replace("Updated: 2020-01-01\n\n", "").replace(
        "# CLAUDE.md — demo\n\n", "intro\n# CLAUDE.md — demo\nprose\n\n"
    )
    target = write(repo / "CLAUDE.md", text)
    assert after_write(target) is None
    assert target.read_text().splitlines()[:5] == ["intro", "# CLAUDE.md — demo", "", f"Updated: {TODAY}", ""]


def test_a_date_inside_a_code_fence_is_an_example_not_the_stamp(repo, after_write):
    example = "\n```markdown\nUpdated: 2020-01-01\n```\n"
    target = write(repo / "CLAUDE.md", GOOD.replace("Updated: 2020-01-01\n\n", "") + example)
    assert after_write(target) is None
    assert target.read_text().splitlines()[2] == f"Updated: {TODAY}"
    assert example in target.read_text()


def test_a_notebook_edit_is_none_of_our_business(repo, after_write):
    """NotebookEdit carries `notebook_path`, not `file_path` — it must not reach the path lookup."""
    assert after_write(repo / "CLAUDE.md", "NotebookEdit", path_key="notebook_path") is None


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


def test_200_lines_pass_and_201_block(repo, after_write):
    assert after_write(write(repo / "CLAUDE.md", padded(200))) is None
    out = after_write(write(repo / "CLAUDE.md", padded(201)))
    assert "201 lines, limit is 200" in out["reason"]


def test_a_dead_path_blocks_when_its_first_segment_is_real(repo, after_write):
    """`src/sub/gone.py` separates "first segment exists" from "parent dir exists": `src/sub` does not."""
    out = after_write(write(repo / "CLAUDE.md", GOOD + "\nSee `src/app.py`, `src/gone.py`, `src/sub/gone.py`.\n"))
    assert "`src/gone.py`" in out["reason"]
    assert "`src/sub/gone.py`" in out["reason"]
    assert "`src/app.py`" not in out["reason"]


def test_path_lookalikes_are_left_alone(repo, after_write):
    """Every span but the last two anchors on the real `src/`, so only the not-a-path filter spares it."""
    spans = [
        "src/<name>/x.py",
        "src/*.py",
        "src/app.py:12",
        "src/{a,b}.py",
        "$HOME/src/x",
        "origin/main",
        "./nope/x.py",
    ]
    text = GOOD + "\nSee " + ", ".join(f"`{span}`" for span in spans) + ", `~nosuchuser/x.py`.\n"
    assert after_write(write(repo / "CLAUDE.md", text)) is None


def test_a_decision_repeated_from_a_parent_blocks(repo, after_write):
    write(repo / "CLAUDE.md", GOOD)
    out = after_write(write(repo / "src" / "CLAUDE.md", GOOD))
    assert "decision already recorded in" in out["reason"]
    assert "stdlib only" in out["reason"]


def test_a_child_differing_only_in_a_wrapped_line_is_clean(repo, after_write):
    write(repo / "CLAUDE.md", GOOD)
    child = GOOD.replace("Rejected: click", "Rejected: typer")
    assert after_write(write(repo / "src" / "CLAUDE.md", child)) is None


def test_a_repeated_decision_that_starts_with_none_still_blocks(repo, after_write):
    """Only the exact placeholder is exempt — `None of…` is a decision like any other."""
    text = GOOD.replace("None at this level.", "- None of the bots may DM a customer.")
    write(repo / "CLAUDE.md", text)
    out = after_write(write(repo / "src" / "CLAUDE.md", text.replace("Stdlib only", "One module per tool")))
    assert "none of the bots may dm a customer." in out["reason"]
    assert "one module per tool" not in out["reason"]


# ── audit CLI ────────────────────────────────────────────────────────────────


def test_audit_of_a_dir_finds_nested_tracked_files_and_skips_untracked(repo, git, run_audit):
    write(repo / "CLAUDE.md", GOOD)
    nested = write(repo / "src" / "CLAUDE.md", "# CLAUDE.md — src\n")
    git(repo, "add", ".")
    git(repo, "commit", "-qm", "docs")
    write(repo / "untracked" / "CLAUDE.md", "# nothing\n")
    status, out = run_audit(repo)
    assert status == 1
    assert f"{nested}: no `Updated: YYYY-MM-DD` line" in out
    assert f"{repo / 'CLAUDE.md'}:" not in out
    assert "untracked" not in out


def test_audit_says_so_when_git_has_never_seen_the_file(repo, run_audit):
    status, out = run_audit(write(repo / "CLAUDE.md", GOOD))
    assert status == 1
    assert out == f"{repo / 'CLAUDE.md'}: never committed — staleness unknown\n"


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
