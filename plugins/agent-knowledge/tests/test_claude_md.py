"""Tests for plugins/agent-knowledge/hooks/claude_md.py, driven through the script itself.

Every input is a prepared file under `fixtures/` — open it to see exactly what the hook was shown.
A test puts one in place as a CLAUDE.md, lets the hook run, and compares the findings it got back.
"""

from datetime import datetime

TODAY = datetime.now().astimezone().date().isoformat()


def findings(out):
    """The findings of a block, one string each — or [] when the hook stayed silent."""
    return [line[2:] for line in out["reason"].splitlines() if line.startswith("- ")] if out else []


def head(path, count):
    return path.read_text(encoding="utf-8").splitlines()[:count]


# ── the hook stays out of everything that is not a CLAUDE.md edit ────────────


def test_other_files_are_ignored_and_untouched(repo, after_write):
    readme = repo / "README.md"
    readme.write_text("# demo\n")
    assert after_write(readme) is None
    assert readme.read_text() == "# demo\n"


def test_a_notebook_edit_is_ignored(repo, after_write):
    """NotebookEdit carries `notebook_path`, not `file_path` — it must not reach the path lookup."""
    assert after_write(repo / "CLAUDE.md", "NotebookEdit", path_key="notebook_path") is None


# ── stamping the date ────────────────────────────────────────────────────────


def test_a_clean_file_is_silent_and_gets_todays_date(repo, place, after_write):
    target = place("clean", repo)
    assert after_write(target) is None
    assert head(target, 3) == ["# CLAUDE.md — demo", "", f"Updated: {TODAY}"]


def test_a_missing_date_is_inserted_under_the_title(repo, place, after_write):
    target = place("no_date", repo)
    assert after_write(target, "Edit") is None
    assert head(target, 5) == ["# CLAUDE.md — demo", "", f"Updated: {TODAY}", "", "## Business decisions"]


def test_the_date_follows_the_title_wherever_the_title_is(repo, place, after_write):
    target = place("title_not_first", repo)
    assert after_write(target) is None
    assert head(target, 6) == [
        "intro line above the title",
        "# CLAUDE.md — demo",
        "",
        f"Updated: {TODAY}",
        "",
        "prose glued to the title",
    ]


def test_a_date_inside_a_code_fence_is_an_example_and_is_left_alone(repo, place, after_write):
    target = place("date_only_in_code_fence", repo)
    assert after_write(target) is None
    assert head(target, 3) == ["# CLAUDE.md — demo", "", f"Updated: {TODAY}"]
    assert "```markdown\nUpdated: 2020-01-01\n```" in target.read_text()


def test_a_file_already_stamped_today_is_not_rewritten(repo, place, after_write):
    target = place("clean", repo)
    after_write(target)
    stamped_at = target.stat().st_mtime_ns
    assert after_write(target) is None
    assert target.stat().st_mtime_ns == stamped_at


# ── findings come back as a block ────────────────────────────────────────────


def test_missing_sections_are_each_named(repo, place, after_write):
    out = after_write(place("missing_sections", repo))
    assert out["decision"] == "block"
    assert findings(out) == ["missing section `## Business decisions`", "missing section `## Technical decisions`"]


def test_an_empty_section_blocks_and_says_what_to_write_instead(repo, place, after_write):
    out = after_write(place("empty_business_section", repo))
    assert findings(out) == ["`## Business decisions` is empty — record the decisions, or write `None at this level.`"]


def test_a_heading_inside_a_code_fence_is_not_a_section(repo, place, after_write):
    out = after_write(place("section_heading_only_in_code_fence", repo))
    assert findings(out) == ["missing section `## Business decisions`"]


def test_200_lines_pass_and_201_block(repo, place, after_write):
    """The one generated input: a 201-line sample file would be unreadable, its length is the point."""
    target = place("clean", repo)
    clean = target.read_text()
    filler = "- filler\n" * (200 - len(clean.splitlines()))

    target.write_text(clean + filler)
    assert after_write(target) is None

    target.write_text(clean + filler + "- one line too many\n")
    out = after_write(target)
    assert len(findings(out)) == 1
    assert findings(out)[0].startswith("201 lines, limit is 200 — ")


def test_a_path_is_dead_when_its_first_segment_is_real_and_the_rest_is_not(repo, place, after_write):
    out = after_write(place("dead_paths", repo))
    assert findings(out) == [
        "referenced path does not exist: `src/gone.py`",
        "referenced path does not exist: `src/sub/gone.py`",
    ]


def test_things_that_only_look_like_paths_are_left_alone(repo, place, after_write):
    assert after_write(place("path_lookalikes", repo)) is None


# ── a decision lives at one level ────────────────────────────────────────────


def test_a_decision_repeated_from_the_parent_blocks(repo, place, after_write):
    parent = place("clean", repo)
    out = after_write(place("clean", repo / "src"))
    assert findings(out) == [
        f"decision already recorded in {parent}: "
        "`**stdlib only** — hooks run on bare python3. rejected: click, a dependency for one flag.`"
    ]


def test_a_decision_differing_only_in_its_wrapped_line_is_not_a_repeat(repo, place, after_write):
    place("clean", repo)
    assert after_write(place("child_differs_only_in_wrapped_line", repo / "src")) is None


def test_only_the_exact_placeholder_is_exempt_from_the_repeat_check(repo, place, after_write):
    """`None at this level.` repeats at every level by design; `None of the bots…` is a decision."""
    parent = place("decision_starting_with_none_parent", repo)
    out = after_write(place("decision_starting_with_none_child", repo / "src"))
    assert findings(out) == [f"decision already recorded in {parent}: `none of the bots may dm a customer.`"]


# ── audit CLI ────────────────────────────────────────────────────────────────


def test_audit_of_a_dir_covers_nested_tracked_files_and_skips_untracked(repo, place, git, run_audit):
    place("clean", repo)
    nested = place("title_only", repo / "src")
    git(repo, "add", ".")
    git(repo, "commit", "-qm", "docs")
    place("title_only", repo / "untracked")

    status, out = run_audit(repo)
    assert status == 1
    assert out.splitlines() == [
        f"{nested}: no `Updated: YYYY-MM-DD` line under the title",
        f"{nested}: missing section `## Business decisions`",
        f"{nested}: missing section `## Technical decisions`",
    ]


def test_audit_says_so_when_git_has_never_seen_the_file(repo, place, run_audit):
    target = place("clean", repo)
    assert run_audit(target) == (1, f"{target}: never committed — staleness unknown\n")


def test_audit_calls_a_file_stale_at_20_commits_past_it(repo, place, git, run_audit):
    target = place("clean", repo)
    git(repo, "add", "CLAUDE.md")
    git(repo, "commit", "-qm", "docs")

    def commit_code(name):
        (repo / "src" / name).write_text("x = 1\n")
        git(repo, "add", ".")
        git(repo, "commit", "-qm", name)

    for n in range(19):
        commit_code(f"m{n}.py")
    assert run_audit(target) == (0, "")

    commit_code("twentieth.py")
    assert run_audit(target) == (1, f"{target}: stale: 20 commits touched this directory since the file last changed\n")
