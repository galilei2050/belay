"""Behavioral tests for usable-ui.

Every test drives the hook through the boundary Claude Code uses — the real script, JSON on
stdin, JSON on stdout — and asserts what the agent would actually receive. Nothing imports
the hook module: the internals stay refactorable, the behavior does not.
"""

import re
from pathlib import Path

import pytest

PLUGIN = Path(__file__).parent.parent
AGENTS_DIR = PLUGIN / "agents"
PROMPTS = sorted(AGENTS_DIR.glob("*.md"))
SKILL = PLUGIN / "skills" / "ui-decisions" / "SKILL.md"

_ROSTER_RE = re.compile(r"`usable-ui:([\w-]+)`")


def context_of(emitted) -> str:
    """What the agent is told. Fails loudly rather than returning '' when nothing was emitted."""
    return emitted["hookSpecificOutput"]["additionalContext"]


def roster_of(emitted) -> set[str]:
    """The reviewer names the agent is told to dispatch."""
    return set(_ROSTER_RE.findall(context_of(emitted)))


# ── the shipped prompts back the roster the agent receives ───────────────────


def test_agent_is_sent_to_every_shipped_reviewer_and_no_other(hook, repo, stage):
    """A name in the roster with no prompt would dispatch a subagent that does not exist."""
    stage(repo, "Button.tsx")
    assert roster_of(hook.bash("git commit -m x", repo)) == {path.stem for path in PROMPTS}


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


@pytest.mark.parametrize("path", ["service.py", "client.ts", "styles.css", "README.md", "data.json"])
def test_editing_anything_else_is_silent(hook, path):
    """A false positive costs the user an interruption on every non-UI file they touch."""
    assert hook.edit(path) is None


@pytest.mark.parametrize("tool", ["Write", "Edit", "MultiEdit", "NotebookEdit"])
def test_every_edit_tool_is_covered(hook, tool):
    """`NotebookEdit` names its target `notebook_path`; the others use `file_path`."""
    assert hook.edit("Widget.tsx", tool_name=tool) is not None


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


@pytest.mark.parametrize(
    "command",
    ["git commit --dry-run -m x", "git status", "git add .", "echo 'git commit'  # not a commit"],
)
def test_a_command_that_does_not_create_a_commit_is_silent(hook, repo, stage, command):
    stage(repo, "Button.tsx")
    assert hook.bash(command, repo) is None


def test_commit_all_reads_the_worktree(hook, repo, stage, git):
    """`-a` stages at commit time, so the index is still empty when the hook fires."""
    stage(repo, "Button.tsx")
    git(repo, "commit", "-qm", "first")
    (repo / "Button.tsx").write_text("<div>changed</div>\n")
    assert roster_of(hook.bash("git commit -am x", repo))


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
