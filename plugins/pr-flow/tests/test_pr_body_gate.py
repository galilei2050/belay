"""Which PR bodies `pr_body_gate.py` lets through, and which Bash calls it has no business touching.

`NARRATED` is a diff told as a list of symbols; `GOOD` is the failure, a comparison table, the
decisions, and checks full of commands. Every threshold is tested from both sides.
"""

import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

NARRATED = """## Defect

Texts to the main line named no shop, so 8 replies in 8 days scheduled no check.

## What changed

- `TasksRepository.shop_of(message=)` mirrors `CallsRepository.shop_of`.
- `ShopEvidence.OPEN_TASK` added.
- `Message.names_a_shop` removed.
- `CheckScheduler` takes `tasks` and uses `shop_of` in `sms_received` / `sms_sent`.
- `CheckRecordLoader` uses `shop_of` for the text's shop.
"""

TABLE = """
| Last 8 days | Before | After |
|---|---|---|
| Replies with a shop | 0 of 8 | 6 of 8 |
| Open tasks that missed a reply | 2 | 0 |
"""

GOOD = f"""## What was wrong

A text to the main line named no shop, so the reply scheduled no follow-up check.
{TABLE}
## What changed

- A text is placed through its customer when the line it hit belongs to no shop.
- A task closed before its check runs ends the check quietly instead of retrying forever.

## Checks

- `ruff format --check app/`, `ruff check app/`, `anon_lint.py --recursive app/`: clean.
- `pytest tests/tasks -q`: 41 passed.
"""

MERMAID = """## What this adds

A reply now reaches the task that asked for it.

```mermaid
flowchart LR
  text["text to main line"] --> customer["customer"] --> task["open task"]
```
"""

# 53 plain words: the density test adds a known number of code spans to it.
FILLER = " ".join(["the reply now reaches the customer task"] * 7) + " and is checked once."


def bullets(coded: int, plain: int) -> str:
    """A What changed list with `coded` symbol bullets followed by `plain` decision bullets."""
    lines = [f"- `Thing{n}.method` changed." for n in range(coded)]
    lines += [f"- A decision number {n} made for a stated reason." for n in range(plain)]
    return "## What changed\n\n" + "\n".join(lines) + "\n"


class FakeMessagesApi:
    """A local Messages API: records each request, answers with the `verdict` tool input it is set to."""

    def __init__(self) -> None:
        self.requests: list[dict] = []
        self.verdict = {"passes": True, "rewrite": ""}
        self.status = 200
        api = self

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self) -> None:
                assert self.path == "/v1/messages"
                assert self.headers["x-api-key"] == "test-key"
                request = json.loads(self.rfile.read(int(self.headers["content-length"])))
                # The real API answers 400 when the forced tool is not declared; so must the fake.
                forced = request["tool_choice"]["name"]
                tool = next(tool for tool in request["tools"] if tool["name"] == forced)
                assert tool["input_schema"]["required"] == ["passes", "rewrite"]
                api.requests.append(request)
                reply = {"content": [{"type": "tool_use", "name": forced, "input": api.verdict}]}
                payload = json.dumps(reply).encode()
                self.send_response(api.status)
                self.send_header("content-type", "application/json")
                self.end_headers()
                self.wfile.write(payload)

            def log_message(self, format: str, *args: object) -> None:  # noqa: A002 — the base's name
                """Keep the test output clean of access-log lines."""

        self.server = HTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self.server.server_port}"


@pytest.fixture(autouse=True)
def api(monkeypatch):
    """Every hook run in this file talks to the fake API, never to the real one."""
    fake = FakeMessagesApi()
    # The default 0.5 s poll is what `shutdown` waits on — half a second per test.
    thread = threading.Thread(target=fake.server.serve_forever, kwargs={"poll_interval": 0.01}, daemon=True)
    thread.start()
    monkeypatch.setenv("ANTHROPIC_BASE_URL", fake.url)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    yield fake
    fake.server.shutdown()


@pytest.fixture
def gate(run_hook, tmp_path):
    """Run the hook over a Bash call from `tmp_path`, with `body` written to `body.md` first."""

    def run(command: str, body: str | None = None):
        if body is not None:
            (tmp_path / "body.md").write_text(body)
        payload = {"hook_event_name": "PreToolUse", "tool_name": "Bash", "tool_input": {"command": command}}
        return run_hook("pr_body_gate.py", payload, tmp_path)

    return run


@pytest.fixture
def check(gate):
    """The deny reason for `gh pr create --body-file body.md` with this body, or None if it passes."""

    def run(body: str) -> str | None:
        output = gate("gh pr create --title t --body-file body.md", body)
        return None if output is None else reason_of(output)

    return run


def reason_of(output) -> str:
    assert output is not None
    payload = output["hookSpecificOutput"]
    assert payload["hookEventName"] == "PreToolUse"
    assert payload["permissionDecision"] == "deny"
    return payload["permissionDecisionReason"]


def test_a_diff_told_as_symbols_is_denied_with_every_rewrite_it_needs(check):
    reason = check(NARRATED)
    assert reason is not None
    assert "1. No before/after" in reason
    assert "2. The bullet list narrates the diff (5 of 5 bullets are identifiers)" in reason
    # The quotes are the most code-dense bullets, so the agent sees exactly what to cut.
    assert "e.g.:\n  - `TasksRepository.shop_of(message=)` mirrors `CallsRepository.shop_of`.\n" in reason
    assert "3. The prose is dense with inline code" in reason


@pytest.mark.parametrize("body", [GOOD, MERMAID, MERMAID.replace("```mermaid", "```mermaid\n%% c")])
def test_a_body_with_a_table_or_a_diagram_passes(check, body):
    assert check(body) is None


def test_a_good_body_without_its_table_gets_only_the_table_finding(check):
    reason = check(GOOD.replace(TABLE, "\n"))
    assert reason is not None
    assert "1. No before/after" in reason
    assert "2." not in reason


def test_pipes_in_prose_are_not_a_table(check):
    reason = check(MERMAID.partition("```")[0] + "Piped as stdout | jq | sort.\n")
    assert reason is not None
    assert "1. No before/after" in reason


@pytest.mark.parametrize("rule", ["|-|-|-|", "--- | --- | ---", "| :-- | :-: | --: |"])
def test_every_gfm_table_rule_counts(check, rule):
    assert check(f"| | Before | After |\n{rule}\n| x | 1 | 2 |\n") is None


@pytest.mark.parametrize("heading", ["Checks", "Verification", "Tests", "Проверки"])
def test_commands_in_the_checks_section_do_not_count(check, heading):
    commands = "".join(f"- `make check-{n}`: green.\n" for n in range(10))
    assert check(GOOD.replace("## Checks", f"## {heading}") + commands) is None


def test_a_heading_that_only_contains_check_is_still_judged(check):
    reason = check(TABLE + "## Checkout flow\n\n" + bullets(coded=5, plain=0))
    assert reason is not None
    assert "narrates the diff" in reason


@pytest.mark.parametrize(
    ("coded", "plain", "denied"),
    [
        (3, 1, True),  # majority coded
        (2, 2, False),  # exactly half is not a majority
        (1, 4, False),  # a decision list that names one symbol
        (3, 0, False),  # under MIN_LIST: too short to be a file list
    ],
)
def test_diff_narration_needs_a_list_mostly_of_symbols(check, coded, plain, denied):
    reason = check(TABLE + bullets(coded, plain))
    assert (reason is not None and "narrates the diff" in reason) is denied


@pytest.mark.parametrize(("spans", "denied"), [(2, False), (3, False), (4, True)])
def test_code_density_allows_a_few_identifiers(check, spans, denied):
    # FILLER is 53 words and a fenced diagram counts none; 3 spans is 3/56, 4 spans is 4/57 (> 6%).
    prose = FILLER + "".join(f" `id{n}`" for n in range(spans))
    reason = check("```mermaid\nflowchart LR\n  a --> b\n```\n" + prose + "\n")
    assert (reason is not None and "dense with inline code" in reason) is denied


@pytest.mark.parametrize(
    "command",
    [
        'gh pr create --title t --body "## Defect"',
        "gh pr create --title t -b x",
        "gh pr create --fill",
        "gh pr create --title t",
        "gh pr edit 12 --body-file -",
        "gh pr create --title t --body-file - <<'EOF'\nbody\nEOF",
        "bash -lc 'gh pr create --title x --body hi'",
        "gh pr edit 12 --add-label bug && gh pr edit 12 --body x",
    ],
)
def test_a_body_that_is_not_a_file_is_denied(gate, command):
    assert "--body-file <path>" in reason_of(gate(command))


@pytest.mark.parametrize(
    "command",
    [
        "gh api repos/o/r/pulls/12 -X PATCH -f body=x",
        'gh api repos/o/r/pulls/12 -X PATCH -f "body=## x"',
        "gh api repos/o/r/pulls/12 -X PATCH --input body.json",
        "gh api graphql -f query='mutation { updatePullRequest(input: {body: \"x\"}) { clientMutationId } }'",
    ],
)
def test_writing_a_body_through_the_api_is_denied(gate, command):
    assert "through `gh api`" in reason_of(gate(command))


def test_a_body_file_written_in_the_same_call_is_denied_until_it_exists(gate, tmp_path):
    reason = reason_of(gate("cat > new.md <<'EOF'\nx\nEOF\ngh pr create --body-file new.md"))
    assert f"`new.md` does not exist at `{tmp_path / 'new.md'}`" in reason


def test_a_quoted_body_file_path_is_read(gate, tmp_path):
    (tmp_path / "my body.md").write_text(NARRATED)
    assert "narrates the diff" in reason_of(gate("gh pr create --title t --body-file 'my body.md'"))


def test_a_body_file_that_is_not_text_is_denied_not_crashed_through(gate, tmp_path):
    (tmp_path / "body.md").write_bytes(b"\xff\xfe\x00bad")
    assert "cannot be read as UTF-8" in reason_of(gate("gh pr create --body-file body.md"))


@pytest.mark.parametrize(
    "command",
    [
        "gh pr edit 12 --add-label bug",
        'gh pr edit 12 --title "drop -b flag"',
        "gh pr edit 12 --add-label team-b",
        "gh pr edit 12 --add-label bug && echo --body done",
        "gh pr view 12 --json body",
        "git commit -m 'run gh pr create --body x later'",
        "gh api repos/o/r/pulls/12 --jq .body",
    ],
)
def test_calls_that_post_no_body_pass(gate, command):
    assert gate(command) is None


JARGON = "Tasks agent: the agent decides a text's shop, or leaves it to both piles"
PLAIN = "Customer texts to the Main line get a follow-up task"


def test_a_title_that_needs_the_code_is_denied_with_a_rewrite(gate, api):
    api.verdict = {"passes": False, "rewrite": PLAIN}
    reason = reason_of(gate(f'gh pr create --title "{JARGON}" --body-file body.md', GOOD))
    assert f"The title `{JARGON}` needs the code to decode" in reason
    assert f"For example:\n  {PLAIN}\n" in reason
    # Sonnet, not Haiku, is the measured choice; it sees the title and the body it describes.
    assert api.requests[0]["model"] == "claude-sonnet-5"
    assert api.requests[0]["messages"][0]["content"] == f"Title: {JARGON}\n\nBody (opening):\n{GOOD}"


def test_the_judge_sees_only_the_opening_of_a_long_body(gate, api):
    long_body = GOOD + "x" * 3000
    gate(f'gh pr create --title "{PLAIN}" --body-file body.md', long_body)
    assert api.requests[0]["messages"][0]["content"] == f"Title: {PLAIN}\n\nBody (opening):\n{long_body[:1500]}"


def test_the_suggested_title_is_accepted_once_without_asking_again(gate, api):
    api.verdict = {"passes": False, "rewrite": PLAIN}
    assert PLAIN in reason_of(gate(f'gh pr create --title "{JARGON}" --body-file body.md', GOOD))
    assert gate(f'gh pr create --title "{PLAIN}" --body-file body.md') is None
    assert len(api.requests) == 1
    gate(f'gh pr create --title "{PLAIN}" --body-file body.md')  # the suggestion was used up
    assert len(api.requests) == 2


def test_a_title_ending_in_a_quote_reaches_the_judge_whole(gate, api):
    gate("gh pr edit 12 --title 'Invoices show \"Paid\"'")
    assert api.requests[0]["messages"][0]["content"].startswith('Title: Invoices show "Paid"\n')


@pytest.mark.parametrize(
    ("case", "error"),
    [
        ("no key", "ANTHROPIC_API_KEY is not set"),
        ("api error", "Messages API call failed"),
        ("empty rewrite", "the judge failed the title but gave no rewrite"),
    ],
)
def test_a_judge_that_cannot_answer_denies_instead_of_letting_the_title_through(gate, api, monkeypatch, case, error):
    if case == "no key":
        monkeypatch.delenv("ANTHROPIC_API_KEY")
    elif case == "api error":
        api.status = 500
    else:
        api.verdict = {"passes": False, "rewrite": " "}
    reason = reason_of(gate(f'gh pr edit 12 --title "{JARGON}"'))
    assert f"The title could not be checked: {error}" in reason


def test_a_rejected_body_in_one_call_is_not_lost_to_a_title_in_another(gate, api):
    api.status = 500
    reason = reason_of(gate(f'gh pr create --title A --body-file body.md; gh pr edit 5 --title "{JARGON}"', NARRATED))
    assert "narrates the diff" in reason
    assert api.requests == []


def test_a_plain_title_passes(gate, api):
    assert gate(f'gh pr edit 12 -t "{PLAIN}"') is None
    assert api.requests[0]["messages"][0]["content"] == f"Title: {PLAIN}\n\nBody (opening):\n"


def test_the_title_is_not_judged_while_the_body_is_rejected(gate, api):
    api.verdict = {"passes": False, "rewrite": PLAIN}
    reason = reason_of(gate(f'gh pr create --title "{JARGON}" --body-file body.md', NARRATED))
    assert "narrates the diff" in reason
    assert "needs the code to decode" not in reason
    assert api.requests == []


def test_calls_without_a_title_do_not_reach_the_judge(gate, api):
    assert gate("gh pr edit 12 --body-file body.md", GOOD) is None
    assert api.requests == []
