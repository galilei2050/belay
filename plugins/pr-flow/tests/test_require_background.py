"""Which Bash calls `require_background.py` denies, and which it has no business touching.

Run at the real boundary — the hook as a subprocess, fed the payload Claude Code sends — because
the deny is the product and its shape (`permissionDecision`, the reason text) is what the harness
reads.
"""

import importlib.util
import sys
from pathlib import Path

import pytest

HOOKS = Path(__file__).parent.parent / "hooks"
SCRIPT = Path(__file__).parent.parent / "scripts" / "ci.py"
sys.path.insert(0, str(HOOKS))  # the hooks import their siblings by bare name, as Claude Code runs them

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


def script_blocking_verbs() -> list[str]:
    """`ci.py`'s own list of blocking verbs — the source the hook copies.

    Driving the deny test from the script rather than from a literal here is what holds the two
    copies level: a verb the script blocks and the hook has not learned about fails at the real
    boundary, not in a comparison of two constants that passes even if the matcher stopped working.
    """
    spec = importlib.util.spec_from_file_location("ci_module", SCRIPT)
    assert spec is not None
    assert spec.loader is not None
    script = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(script)
    return list(script.BLOCKING_VERBS)


@pytest.mark.parametrize("verb", script_blocking_verbs())
def test_every_blocking_verb_is_denied_in_the_foreground(gate, verb):
    output = gate(f"{CI} {verb}")
    assert output is not None
    payload = output["hookSpecificOutput"]
    # The whole mapping, not just the decision: a deny naming the wrong event is one the harness
    # ignores, and it would look identical to a working one from inside a test.
    assert payload["hookEventName"] == "PreToolUse"
    assert payload["permissionDecision"] == "deny"
    assert "run_in_background" in payload["permissionDecisionReason"]


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
    """A quoted mention — an echo, a commit message — is not a call."""
    assert gate(f'echo "launch {CI} ship in the background"') is None


@pytest.mark.parametrize(
    "wrapper",
    [
        "bash -c '{cmd}'",
        'sh -c "{cmd}"',
        "eval '{cmd}'",
        "timeout 7200 bash -c '{cmd}'",
        "bash -lc '{cmd}'",  # the commoner idiom, and a bare-`-c` pattern let it through
        "bash -ec '{cmd}'",
        "bash --norc -c '{cmd}'",
    ],
)
def test_a_shell_wrapped_call_does_not_slip_past_the_quotes(gate, wrapper):
    """`sh -c '…'` is a quoted span that IS the command — blanking it would leave the gate open."""
    output = gate(wrapper.format(cmd=f"{CI} ship"))
    assert output is not None
    assert output["hookSpecificOutput"]["permissionDecision"] == "deny"


def test_a_heredoc_mentioning_the_command_is_denied_too(gate):
    """A known, loud false positive, recorded rather than left to be rediscovered.

    A heredoc body is not a quoted span, so prose inside one reads as a command. Telling "writes
    the line into a file" apart from "runs it" needs a real shell parser; until something needs
    that, a deny the agent can rephrase past beats a gate with a hole in it.
    """
    output = gate(f"cat <<'EOF' > notes.md\nlaunch {CI} ship in the background\nEOF")
    assert output is not None
    assert output["hookSpecificOutput"]["permissionDecision"] == "deny"


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
