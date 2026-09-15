"""Tests for plugins/comment-guard-hook/hooks/comment_guard_hook.py.

Decisions are driven through `main()` rather than `check()`, so each case asserts the payload
Claude Code actually receives, and exercises payload -> replay -> diff -> AST -> emit as one path.
"""

import io
import json
import textwrap

import comment_guard_hook
import pytest
from comment_guard_hook import MAX_LINES

OVER = MAX_LINES + 1


def via_main(monkeypatch, capsys, tool_name, file_path, **tool_input):
    """Run the hook over a synthesised PreToolUse payload; returns the emitted JSON or None."""
    payload = json.dumps({"tool_name": tool_name, "tool_input": {"file_path": str(file_path), **tool_input}})
    monkeypatch.setattr("sys.stdin", io.StringIO(payload))
    comment_guard_hook.main()
    out = capsys.readouterr().out
    return json.loads(out) if out.strip() else None


def write_py(monkeypatch, capsys, tmp_path, source, name="mod.py"):
    """Put `source` through a Write of `name`, as if the agent were creating the file."""
    return via_main(monkeypatch, capsys, "Write", tmp_path / name, content=textwrap.dedent(source))


def denied_lines(out):
    return out["hookSpecificOutput"]["permissionDecisionReason"]


# ── the cap itself ───────────────────────────────────────────────────────────


def test_function_docstring_over_the_cap_is_denied(monkeypatch, capsys, tmp_path):
    out = write_py(
        monkeypatch,
        capsys,
        tmp_path,
        '''
        def route(call):
            """One.
            Two.
            Three.
            Four.
            """
            return call
        ''',
    )
    assert out["hookSpecificOutput"]["hookEventName"] == "PreToolUse"  # how the harness routes it
    assert out["hookSpecificOutput"]["permissionDecision"] == "deny"
    assert "`route`" in denied_lines(out)
    assert f"{OVER} lines" in denied_lines(out)


def test_docstring_at_the_cap_passes(monkeypatch, capsys, tmp_path):
    out = write_py(
        monkeypatch,
        capsys,
        tmp_path,
        '''
        def route(call):
            """One.
            Two.
            Three.
            """
            return call
        ''',
    )
    assert out is None


def test_blank_lines_inside_a_docstring_are_not_counted(monkeypatch, capsys, tmp_path):
    """PEP 257's summary-blank-body shape must not cost a line of the budget."""
    out = write_py(
        monkeypatch,
        capsys,
        tmp_path,
        '''
        def route(call):
            """Summary.

            Body.
            """
            return call
        ''',
    )
    assert out is None


def test_class_docstring_is_capped_too(monkeypatch, capsys, tmp_path):
    out = write_py(
        monkeypatch,
        capsys,
        tmp_path,
        '''
        class Router:
            """One.
            Two.
            Three.
            Four.
            """
        ''',
    )
    assert "`Router`" in denied_lines(out)


def test_module_docstring_is_exempt(monkeypatch, capsys, tmp_path):
    """One orientation paragraph per file has no signature that could carry it instead."""
    out = write_py(
        monkeypatch,
        capsys,
        tmp_path,
        '''
        """One.
        Two.
        Three.
        Four.
        Five.
        """

        def route(call):
            return call
        ''',
    )
    assert out is None


def test_async_def_is_capped(monkeypatch, capsys, tmp_path):
    out = write_py(
        monkeypatch,
        capsys,
        tmp_path,
        '''
        async def route(call):
            """One.
            Two.
            Three.
            Four.
            """
            return call
        ''',
    )
    assert "`route`" in denied_lines(out)


# ── the comment-block escape route ───────────────────────────────────────────


def test_a_comment_block_over_the_cap_is_denied(monkeypatch, capsys, tmp_path):
    """Without this the essay just moves from inside the quotes to the lines above them."""
    out = write_py(
        monkeypatch,
        capsys,
        tmp_path,
        """
        # One.
        # Two.
        # Three.
        # Four.
        def route(call):
            return call
        """,
    )
    assert "comment block" in denied_lines(out)


def test_a_comment_block_at_the_cap_passes(monkeypatch, capsys, tmp_path):
    out = write_py(
        monkeypatch,
        capsys,
        tmp_path,
        """
        # One.
        # Two.
        # Three.
        def route(call):
            return call
        """,
    )
    assert out is None


def test_trailing_comments_are_not_a_block(monkeypatch, capsys, tmp_path):
    """Each annotates its own line of code, so consecutive ones are not one essay."""
    out = write_py(
        monkeypatch,
        capsys,
        tmp_path,
        """
        a = 1  # one
        b = 2  # two
        c = 3  # three
        d = 4  # four
        """,
    )
    assert out is None


def test_blank_line_separates_two_comment_blocks(monkeypatch, capsys, tmp_path):
    out = write_py(
        monkeypatch,
        capsys,
        tmp_path,
        """
        # One.
        # Two.

        # Three.
        # Four.
        a = 1
        """,
    )
    assert out is None


# ── scope: only what this edit touches, only Python ──────────────────────────


def test_editing_elsewhere_in_a_file_with_a_long_docstring_is_allowed(monkeypatch, capsys, tmp_path):
    """Opening an old file to change one line must not be denied over prose written years ago."""
    target = tmp_path / "legacy.py"
    target.write_text(
        textwrap.dedent(
            '''
            def route(call):
                """One.
                Two.
                Three.
                Four.
                """
                return call
            ''',
        ),
        encoding="utf-8",
    )
    out = via_main(monkeypatch, capsys, "Edit", target, old_string="return call", new_string="return call.id")
    assert out is None


def test_editing_the_long_docstring_itself_is_denied(monkeypatch, capsys, tmp_path):
    target = tmp_path / "legacy.py"
    target.write_text(
        textwrap.dedent(
            '''
            def route(call):
                """One.
                Two.
                """
                return call
            ''',
        ),
        encoding="utf-8",
    )
    out = via_main(
        monkeypatch, capsys, "Edit", target, old_string="    Two.\n", new_string="    Two.\n    Three.\n    Four.\n"
    )
    assert "`route`" in denied_lines(out)


def test_multiedit_is_judged_on_the_result_of_every_edit(monkeypatch, capsys, tmp_path):
    target = tmp_path / "mod.py"
    target.write_text('def route(call):\n    """One."""\n    return call\n', encoding="utf-8")
    out = via_main(
        monkeypatch,
        capsys,
        "MultiEdit",
        target,
        edits=[
            {"old_string": '"""One."""', "new_string": '"""One.\n    Two.\n    Three."""'},
            {"old_string": "    Three.", "new_string": "    Three.\n    Four."},
        ],
    )
    assert "`route`" in denied_lines(out)


@pytest.mark.parametrize("name", ["notes.md", "data.json", "script.sh"])
def test_non_python_files_are_left_alone(monkeypatch, capsys, tmp_path, name):
    out = via_main(monkeypatch, capsys, "Write", tmp_path / name, content="# One\n# Two\n# Three\n# Four\n")
    assert out is None


def test_unparseable_source_is_left_to_the_next_edit(monkeypatch, capsys, tmp_path):
    out = write_py(monkeypatch, capsys, tmp_path, 'def route(\n    """One.\n    Two.\n    Three.\n    Four.\n    """\n')
    assert out is None


# ── the reason has to teach ──────────────────────────────────────────────────


def test_the_reason_says_what_a_docstring_is_for_and_where_the_rest_goes(monkeypatch, capsys, tmp_path):
    out = write_py(
        monkeypatch,
        capsys,
        tmp_path,
        '''
        def route(call):
            """One.
            Two.
            Three.
            Four.
            """
            return call
        ''',
    )
    reason = denied_lines(out)
    assert "how to call it and what you get back" in reason
    assert "CLAUDE.md" in reason
    assert "commit message" in reason


def test_every_offender_is_listed_with_its_length(monkeypatch, capsys, tmp_path):
    out = write_py(
        monkeypatch,
        capsys,
        tmp_path,
        '''
        class Router:
            """One.
            Two.
            Three.
            Four.
            """

            def route(self, call):
                """One.
                Two.
                Three.
                Four.
                Five.
                """
                return call
        ''',
    )
    reason = denied_lines(out)
    assert "`Router`" in reason
    assert "`route`" in reason
    assert f"{OVER + 1} lines" in reason
