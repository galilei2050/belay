"""Behavioral tests for usable-ui.

Every test that exercises the hook drives it through the boundary Claude Code uses — the
real script, JSON on stdin, JSON on stdout — and asserts what the agent would actually
receive. Nothing imports the hook module: the internals stay refactorable, the behavior does
not. The remaining tests read the shipped prompts and config directly, because those are
artifacts the loader reads rather than anything the hook computes.
"""

import json
import re
from pathlib import Path

import pytest

PLUGIN = Path(__file__).parent.parent
AGENTS_DIR = PLUGIN / "agents"
PROMPTS = sorted(AGENTS_DIR.glob("*.md"))
SKILL = PLUGIN / "skills" / "ui-decisions" / "SKILL.md"
UI_REVIEW_COMMAND = PLUGIN / "commands" / "ui-review.md"
HOOKS_JSON = PLUGIN / "hooks" / "hooks.json"

_ROSTER_RE = re.compile(r"`usable-ui:([\w-]+)`")


def context_of(emitted) -> str:
    """What the agent is told. Fails loudly rather than returning '' when nothing was emitted."""
    return emitted["hookSpecificOutput"]["additionalContext"]


def roster_of(emitted) -> set[str]:
    """The reviewer names the agent is told to dispatch."""
    return set(_ROSTER_RE.findall(context_of(emitted)))


def matched_tools() -> list[str]:
    """The tools Claude Code will actually run this hook for, read from the shipped config."""
    entries = json.loads(HOOKS_JSON.read_text())["hooks"]["PreToolUse"]
    return [tool for entry in entries for tool in entry["matcher"].split("|")]


# ── the shipped prompts back the roster the agent receives ───────────────────


def test_agent_is_sent_to_every_shipped_reviewer_and_no_other(hook, repo, stage):
    """A name in the roster with no prompt would dispatch a subagent that does not exist."""
    stage(repo, "Button.tsx")
    assert roster_of(hook.bash("git commit -m x", repo)) == {path.stem for path in PROMPTS}


def test_the_slash_command_dispatches_the_same_panel():
    """`/ui-review` names the five by hand; the same ghost-subagent bug is reachable through it."""
    names = set(_ROSTER_RE.findall(UI_REVIEW_COMMAND.read_text()))
    assert names - {"ui-decisions"} == {path.stem for path in PROMPTS}


@pytest.mark.parametrize("prompt", PROMPTS, ids=lambda p: p.stem)
def test_prompt_declares_its_own_name_and_stays_read_only(prompt):
    """Claude Code resolves the agent by its frontmatter `name`, not by the filename."""
    frontmatter = prompt.read_text().split("---")[1]
    assert f"name: {prompt.stem}\n" in frontmatter
    assert "disallowedTools: Write, Edit, NotebookEdit" in frontmatter


@pytest.mark.parametrize("prompt", PROMPTS, ids=lambda p: p.stem)
def test_prompt_demands_the_clean_verdict(prompt):
    """The merge step depends on a clean reviewer saying exactly this and nothing else."""
    assert "`NO FINDINGS`" in prompt.read_text()


def test_skill_declares_the_name_the_hook_tells_the_agent_to_invoke(hook):
    """The nudge names the skill; a mismatch sends the agent after something that isn't there."""
    assert "name: ui-decisions\n" in SKILL.read_text().split("---")[1]
    assert "usable-ui:ui-decisions" in context_of(hook.edit("/x/Button.tsx"))


# ── the write-time nudge ─────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "path",
    ["Button.tsx", "app/page.jsx", "Card.vue", "x.svelte", "index.html", "mail.jinja2", "user.blade.php"],
)
def test_editing_a_user_facing_file_hands_over_the_rules(hook, path):
    assert "ui-decisions" in context_of(hook.edit(path))


def test_the_nudge_names_the_file_being_edited(hook):
    """Without the path the agent has to guess which of its pending edits this is about."""
    assert "src/Cart.tsx" in context_of(hook.edit("src/Cart.tsx"))


@pytest.mark.parametrize("path", ["service.py", "client.ts", "styles.css", "README.md", "data.json"])
def test_editing_anything_else_is_silent(hook, path):
    """A false positive costs the user an interruption on every non-UI file they touch."""
    assert hook.edit(path) is None


@pytest.mark.parametrize("tool", ["Write", "Edit", "MultiEdit"])
def test_every_matched_edit_tool_is_covered(hook, tool):
    assert hook.edit("Widget.tsx", tool_name=tool) is not None


def test_the_tools_under_test_are_the_tools_the_loader_dispatches():
    """A matcher listing a tool the hook ignores spawns a process that can only exit silently."""
    assert set(matched_tools()) == {"Write", "Edit", "MultiEdit", "Bash"}


def test_the_rules_are_handed_over_once_per_session(hook):
    """Repeating them on every component teaches the agent to skim past them."""
    assert hook.edit("First.tsx") is not None
    assert hook.edit("Second.tsx") is None


def test_a_new_session_gets_the_rules_again(hook):
    """Context does not survive a session, so the nudge has to cross it."""
    assert hook.edit("First.tsx", session_id="s1") is not None
    assert hook.edit("Second.tsx", session_id="s2") is not None


# ── the commit-time panel ────────────────────────────────────────────────────


def test_committing_user_facing_files_dispatches_the_panel(hook, repo, stage):
    stage(repo, "src/Button.tsx")
    assert roster_of(hook.bash("git commit -m x", repo))


def test_committing_no_user_facing_files_is_silent(hook, repo, stage):
    """Five subagents are the user's money; a backend-only commit does not owe them."""
    stage(repo, "service.py", content="y = 2\n")
    assert hook.bash("git commit -m x", repo) is None


def test_the_panel_is_told_which_files_are_user_facing(hook, repo, stage):
    """A mixed commit still gets reviewed, but the roster points at the UI, not the whole diff."""
    stage(repo, "src/Button.tsx")
    stage(repo, "service.py", content="y = 2\n")
    context = context_of(hook.bash("git commit -m x", repo))
    assert "src/Button.tsx" in context
    assert "service.py" not in context


def test_a_non_ascii_filename_is_still_a_user_facing_file(hook, repo, stage):
    """`git diff --name-only` C-quotes non-ASCII paths, and a quoted name matches no suffix."""
    stage(repo, "Кнопка.tsx")
    assert "Кнопка.tsx" in context_of(hook.bash("git commit -m x", repo))


@pytest.mark.parametrize(
    "command",
    [
        "git commit -m x",
        "make lint && git commit -F .scratch/COMMIT_MSG",
        "git --no-pager commit -m x",
        "git commit --amend --no-edit",
        # A newline separates two commands exactly like `;` does, and the agent writes commits this way.
        "git add -A\ngit commit -m x",
        "make lint\nmake test\ngit commit -m x",
        # The dry run belongs to the push, not to the commit standing beside it.
        "git commit -m x && git push --dry-run",
        # The cases that made this hook and review-panel disagree: the real commit is the
        # second segment, and `commit -C` reuses a message rather than naming another repo.
        "git commit --dry-run && git commit -m x",
        "git -C /elsewhere commit -m x && git commit -m y",
        "git commit -C HEAD~1",
    ],
)
def test_every_commit_form_the_agent_writes_dispatches_the_panel(hook, repo, stage, command):
    stage(repo, "Button.tsx")
    assert roster_of(hook.bash(command, repo))


@pytest.mark.parametrize(
    "command",
    [
        "git commit --dry-run -m x",
        "git status",
        "git add .",
        "echo 'git commit'  # not a commit",
        "git -C /other/repo commit -m x",
    ],
)
def test_a_command_that_does_not_commit_here_is_silent(hook, repo, stage, command):
    """`-C` commits into a repo the payload's cwd does not name — the panel would read the wrong HEAD."""
    stage(repo, "Button.tsx")
    assert hook.bash(command, repo) is None


def test_commit_all_reads_the_worktree(hook, repo, stage, git):
    """`-a` stages at commit time, so the index is still empty when the hook fires."""
    stage(repo, "Button.tsx")
    git(repo, "commit", "-qm", "first")
    (repo / "Button.tsx").write_text("<div>changed</div>\n")
    assert roster_of(hook.bash("git commit -am x", repo))


def test_a_flag_named_in_the_commit_message_is_not_a_flag(hook, repo, stage, git):
    """`-m "fix -a flag"` must not widen the scope to the worktree, nor `--dry-run` silence it."""
    stage(repo, "Button.tsx")
    git(repo, "commit", "-qm", "first")
    stage(repo, "service.py", content="y = 2\n")
    (repo / "Button.tsx").write_text("<div>unstaged</div>\n")
    assert hook.bash('git commit -m "fix the -a flag"', repo) is None
    assert hook.bash('git commit -m "document --dry-run"', repo) is None


def test_an_identical_retry_stays_silent(hook, repo, stage):
    """A commit rejected by pre-commit and retried carries the same UI; re-dispatching is waste."""
    stage(repo, "Button.tsx")
    assert hook.bash("git commit -m x", repo) is not None
    assert hook.bash("git commit -m x", repo) is None


def test_changed_ui_dispatches_the_panel_again(hook, repo, stage):
    """Once the agent edits something, the panel has new content to read."""
    stage(repo, "Button.tsx")
    assert hook.bash("git commit -m x", repo) is not None
    stage(repo, "Button.tsx", content="<div>fixed</div>\n")
    assert hook.bash("git commit -m x", repo) is not None


def test_outside_a_git_repo_the_hook_says_nothing(hook, tmp_path):
    """The commit will fail on its own; the hook does not need to explain why."""
    assert hook.bash("git commit -m x", tmp_path) is None


# ── the hook never takes the permission decision ─────────────────────────────


def test_the_hook_is_advisory_and_never_decides_permission(hook, repo, stage):
    """`allow` would bypass acl-hook and the user's settings; `deny` would gate every commit."""
    stage(repo, "Button.tsx")
    emitted = hook.bash("git commit -m x", repo)
    assert "permissionDecision" not in emitted["hookSpecificOutput"]
    assert set(emitted) == {"hookSpecificOutput"}
    assert "permissionDecision" not in context_of(hook.edit("Other.tsx"))
