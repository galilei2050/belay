"""Which Bash calls `require_background.py` denies, and which it has no business touching.

Run at the real boundary — the hook as a subprocess, fed the payload Claude Code sends — because
the deny is the product and its shape (`permissionDecision`, the reason text) is what the harness
reads.
"""

import pytest

CI = "python3 /home/x/.claude/plugins/pr-flow/scripts/ci.py"


@pytest.fixture
def gate(run_hook, tmp_path):
    """Run the hook over a Bash call, foreground unless `background=True`."""

    def run(command: str, *, background: bool = False, event: str = "PreToolUse", tool: str = "Bash"):
        tool_input: dict[str, object] = {"command": command}
        if background:
            tool_input["run_in_background"] = True
        payload = {"hook_event_name": event, "tool_name": tool, "tool_input": tool_input}
        return run_hook("require_background.py", payload, tmp_path)

    return run


@pytest.mark.parametrize("verb", ["wait", "merged", "deploy", "ship"])
def test_every_blocking_verb_is_denied_in_the_foreground(gate, verb):
    output = gate(f"{CI} {verb}")
    assert output is not None
    assert output["hookSpecificOutput"]["permissionDecision"] == "deny"
    assert "run_in_background" in output["hookSpecificOutput"]["permissionDecisionReason"]


def test_the_reason_names_the_verb_that_was_denied(gate):
    output = gate(f"{CI} ship --region us-central1")
    assert output is not None
    assert "ci.py ship" in output["hookSpecificOutput"]["permissionDecisionReason"]


def test_the_same_call_in_the_background_is_allowed(gate):
    """The point of the hook is the mechanism, not the command — backgrounded, it must pass."""
    assert gate(f"{CI} ship", background=True) is None


@pytest.mark.parametrize("command", [f"{CI} status", f"{CI} logs --lines 5", f"{CI}"])
def test_the_verbs_that_answer_immediately_stay_foreground(gate, command):
    assert gate(command) is None


def test_an_unrelated_bash_call_is_none_of_its_business(gate):
    assert gate("git push -u origin feature") is None


def test_talking_about_the_command_is_not_running_it(gate):
    """A quoted mention — an echo, a commit message, a heredoc of instructions — is not a call."""
    assert gate(f'echo "launch {CI} ship in the background"') is None


def test_a_blocking_verb_anywhere_in_a_chain_is_still_denied(gate):
    """`&&`-chaining is the obvious way around a command-prefix match, so it is matched per call."""
    output = gate(f"git push && {CI} wait")
    assert output is not None
    assert output["hookSpecificOutput"]["permissionDecision"] == "deny"


def test_a_shell_ampersand_is_not_the_background_this_means(gate):
    """A detached shell job loses its output and has to be polled; the harness flag does not."""
    output = gate(f"{CI} ship &")
    assert output is not None
    assert output["hookSpecificOutput"]["permissionDecision"] == "deny"


def test_another_tool_is_left_alone(gate):
    assert gate(f"{CI} ship", tool="Read") is None


def test_wrong_event_is_silent(gate):
    """The hooks in this plugin do not answer each other's events, whatever ends up wired to them."""
    assert gate(f"{CI} ship", event="PostToolUse") is None
