"""Which PR bodies `pr_body_gate.py` lets through, and which Bash calls it has no business touching.

The bodies are shaped on real ones: `NARRATED` is a diff told as a list of symbols, the shape that
started this hook; `GOOD` is the failure, a before/after table, decisions, and checks full of commands.
"""

import pytest

NARRATED = """## Defect

Texts to the main line named no shop, so 8 replies in 8 days scheduled no check.

## What changed

- `TasksRepository.shop_of(message=)` mirrors `CallsRepository.shop_of`.
- `ShopEvidence.OPEN_TASK` added.
- `Message.names_a_shop` removed.
- `CheckScheduler` takes `tasks` and uses `shop_of` in `sms_received` / `sms_sent`.
- `CheckRecordLoader` uses `shop_of` for the text's shop.

## Rollback

Revert this PR.
"""

GOOD = """## What was wrong

A text to the main line named no shop, so the reply scheduled no follow-up check.

| Last 8 days | Before | After |
|---|---|---|
| Replies with a shop | 0 of 8 | 6 of 8 |
| Open tasks that missed a reply | 2 | 0 |

## What changed

- A text is placed through its customer when the line it hit belongs to no shop.
- A task closed before its check runs ends the check quietly instead of retrying forever.

## Checks

- `ruff format --check app/`, `ruff check app/`, `anon_lint.py --recursive app/`: clean.
- `pytest tests/tasks -q`: 41 passed.
- `make drift-test`: 4/4 against prod.
"""

MERMAID = """## What this adds

A reply now reaches the task that asked for it.

```mermaid
flowchart LR
  text["text to main line"] --> customer["customer"] --> task["open task"]
```
"""


@pytest.fixture
def gate(run_hook, tmp_path):
    """Run the hook over a Bash call from `tmp_path`, with `body` written to `body.md` first."""

    def run(command: str, body: str | None = None, event: str = "PreToolUse"):
        if body is not None:
            (tmp_path / "body.md").write_text(body)
        payload = {"hook_event_name": event, "tool_name": "Bash", "tool_input": {"command": command}}
        return run_hook("pr_body_gate.py", payload, tmp_path)

    return run


def reason_of(output) -> str:
    assert output is not None
    payload = output["hookSpecificOutput"]
    assert payload["hookEventName"] == "PreToolUse"
    assert payload["permissionDecision"] == "deny"
    return payload["permissionDecisionReason"]


def test_a_diff_told_as_symbols_is_denied_with_every_rewrite_it_needs(gate):
    reason = reason_of(gate("gh pr create --title t --body-file body.md", NARRATED))
    assert "1. No before/after" in reason
    assert "2. The bullet list narrates the diff (5 of 5 bullets are identifiers)" in reason
    # The quotes are the most code-dense bullets, so the agent sees exactly what to cut.
    assert "e.g.:\n  - `TasksRepository.shop_of(message=)` mirrors `CallsRepository.shop_of`.\n" in reason
    assert "3. The prose is dense with inline code" in reason


@pytest.mark.parametrize("body", [GOOD, MERMAID])
def test_a_body_with_before_after_or_a_diagram_passes(gate, body):
    assert gate("gh pr create --title t --body-file body.md", body) is None


def test_commands_in_the_checks_section_do_not_count_as_code_soup(gate):
    checks_heavy = GOOD + "".join(f"- `make check-{n}`: green.\n" for n in range(10))
    assert gate("gh pr edit 12 -F body.md", checks_heavy) is None


def test_a_missing_table_alone_is_enough_to_deny(gate):
    reason = reason_of(gate("gh pr edit 12 --body-file=body.md", GOOD.partition("| Last")[0]))
    assert "1. No before/after" in reason
    assert "2." not in reason


@pytest.mark.parametrize(
    "command",
    [
        'gh pr create --title t --body "## Defect"',
        "gh pr create --title t -b x",
        "gh pr create --fill",
        "gh pr create --title t",
        "gh pr edit 12 --body-file -",
        "gh pr create --title t --body-file - <<'EOF'\nbody\nEOF",
        "gh api repos/o/r/pulls/12 -X PATCH -f body=x",
    ],
)
def test_a_body_that_is_not_a_readable_file_is_denied(gate, command):
    assert "--body-file <path>" in reason_of(gate(command))


def test_a_body_file_written_in_the_same_call_is_denied_until_it_exists(gate):
    reason = reason_of(gate("cat > new.md <<'EOF'\nx\nEOF\ngh pr create --body-file new.md"))
    assert "`new.md` does not exist yet" in reason


@pytest.mark.parametrize(
    "command",
    [
        "gh pr edit 12 --add-label bug",
        "gh pr view 12 --json body",
        "git commit -m 'run gh pr create --body x later'",
        "gh api repos/o/r/pulls/12 --jq .body",
    ],
)
def test_calls_that_post_no_body_pass(gate, command):
    assert gate(command) is None


def test_only_pre_tool_use_is_judged(gate):
    assert gate('gh pr create --body "x"', event="PostToolUse") is None
