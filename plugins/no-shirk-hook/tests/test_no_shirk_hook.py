"""Tests for plugins/no-shirk-hook/hooks/no_shirk_hook.py.

The hook is pure: stdin JSON + transcript file → stdout JSON. We test classify()
directly for per-pattern assertions, and main() through a synthesised stdin
payload pointing at a temp JSONL transcript for end-to-end behaviour.
"""

from __future__ import annotations

import io
import json

import no_shirk_hook
import pytest
from no_shirk_hook import (
    build_reason,
    classify,
    extract_tail,
    has_business_ambiguity,
    has_destructive_context,
    last_assistant_text,
    last_user_text,
    match_shirk,
    normalize,
)

# ── normalize / extract_tail ─────────────────────────────────────────────────


def test_normalize_strips_fenced_code():
    text = "report\n\n```python\nshould I run?\n```\n\nthat's it"
    assert "should I run" not in normalize(text)


def test_normalize_strips_inline_code():
    text = "see `запустить?` — that's the question"
    assert "запустить?" not in normalize(text)


def test_normalize_strips_quoted_lines():
    text = "I said:\n> хотите, я сделаю это?\nthat was a quote"
    assert "хотите" not in normalize(text)


def test_extract_tail_returns_last_paragraph():
    text = (
        "First para line.\n\nSecond para.\n\nFinal paragraph here with enough words to clear the short-tail threshold."
    )
    assert extract_tail(text).startswith("Final paragraph here")


def test_extract_tail_grabs_previous_when_short():
    text = "Long paragraph with content.\n\nЗапустить?"
    tail = extract_tail(text)
    assert "Long paragraph" in tail
    assert "Запустить" in tail


# ── shirk pattern positives (RU + EN, per group) ─────────────────────────────


@pytest.mark.parametrize(
    "text",
    [
        "Готово. Запустить?",
        "ruff зелёный. Прогнать тесты?",
        "Done. Should I run the tests?",
        "Tests would catch this — want me to run them?",
    ],
)
def test_ask_to_run_triggers(text):
    assert match_shirk(text) is not None


@pytest.mark.parametrize(
    "text",
    [
        "Хотите, я сделаю это сейчас?",
        "Хочешь, запущу проверку?",
        "Если хочешь, запущу typecheck.",
        "Want me to fix the second file too?",
        "Would you like me to continue with the refactor?",
        "Do you want me to add tests?",
    ],
)
def test_want_me_to_triggers(text):
    assert match_shirk(text) is not None


@pytest.mark.parametrize(
    "text",
    [
        # real-world misses: offering to babysit a CI run and report back
        "Хочешь — последю за прогоном и доложу, зелёный или нет?",
        "Последить за ними и доложить, зелёные ли (первый прогон workflow)?",
        "Want me to watch the run and report whether it's green?",
        "CI is running. Should I keep an eye on it and let you know if it goes red?",
    ],
)
def test_watch_and_report_triggers(text):
    assert match_shirk(text) is not None


def test_watch_and_report_past_tense_is_ok():
    # A completed report (past tense) is not a shirk — it's the result.
    verdict, _, _ = classify("Я последил за прогоном и доложил: 3/3 зелёные.", user_text="")
    assert verdict == "ok"


@pytest.mark.parametrize(
    "text",
    [
        "Если хотите, я допишу остальное.",
        "If you'd like, I can also add error handling.",
        "I can refactor the helper if you'd like.",
    ],
)
def test_if_you_want_triggers(text):
    assert match_shirk(text) is not None


@pytest.mark.parametrize(
    "text",
    [
        "Готов выполнять план.",
        "Готова приступить к рефакторингу.",
        "Ready to proceed when you confirm.",
    ],
)
def test_ready_to_triggers(text):
    assert match_shirk(text) is not None


@pytest.mark.parametrize(
    "text",
    [
        "Скажи если надо доделать второй модуль.",
        "Дайте знать, если нужно подправить.",
        "Let me know if I should run the tests.",
        "Let me know if you'd like a refactor.",
        # "ping me and I'll do it" — 2nd-person-future offer, not imperative
        "Менять-коммитить не стал — скажешь, оформлю коммит.",
        "Коммит не делал. Скажешь — закоммичу.",
        "Done. Just say the word and I'll commit.",
    ],
)
def test_tell_me_if_triggers(text):
    assert match_shirk(text) is not None


@pytest.mark.parametrize(
    "text",
    [
        "Я закоммитил и запушил — всё на месте.",  # past-tense statement
        "Скажу честно: тесты зелёные, линт чист.",  # 1st person, not an offer
    ],
)
def test_tell_me_if_does_not_overmatch(text):
    verdict, _, _ = classify(text, user_text="")
    assert verdict == "ok"


@pytest.mark.parametrize(
    "text",
    [
        "Когда будете готовы, продолжим.",
        "When you're ready, just say go.",
    ],
)
def test_when_youre_ready_triggers(text):
    assert match_shirk(text) is not None


@pytest.mark.parametrize(
    "text",
    [
        "Создаю файл — создаю?",
        "Запускаю?",
        "Делаю?",
        "Продолжить?",
        "Shall I proceed?",
        "Proceed?",
    ],
)
def test_proceed_q_triggers(text):
    assert match_shirk(text) is not None


@pytest.mark.parametrize(
    "text",
    [
        "Изменение workflow пока не закоммичено — коммитить?",
        "Готово локально. Закоммитить?",
        "Все зелёное. Запушить?",
        "Done. Commit?",
        "Ready locally. Push it?",
        "Shall I commit the change?",
    ],
)
def test_commit_offer_triggers(text):
    assert match_shirk(text) is not None


@pytest.mark.parametrize(
    "text",
    [
        "Закоммитил и запушил — всё на месте.",  # past tense
        "Не коммитил — по политике, закоммичу по запросу.",  # statement, mid-line verb
    ],
)
def test_commit_offer_does_not_overmatch(text):
    verdict, _, _ = classify(text, user_text="")
    assert verdict == "ok"


def test_out_of_scope_triggers():
    assert match_shirk("Это вне рамок текущей задачи.") is not None
    assert match_shirk("That fix is out of scope here.") is not None


def test_preexisting_triggers():
    assert match_shirk("Это ранее существовавший баг, не от меня.") is not None
    assert match_shirk("That's a pre-existing issue, not from this change.") is not None
    assert match_shirk("Pre-existing bug — out of scope.") is not None


def test_partial_done_triggers():
    assert match_shirk("Основная функциональность реализована.") is not None
    assert match_shirk("That implements the core flow.") is not None
    assert match_shirk("This should get you started with the migration.") is not None


# ── pattern negatives ────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "text",
    [
        "Готово. Все 14 тестов зелёные, линт чист.",
        "Done — 14/14 tests pass, lint clean.",
        "Применил правки и перезапустил тесты — всё ок.",
        "Refactored 3 files and verified everything still works.",
        "Здесь использую `proceed?` как пример из кода.",
    ],
)
def test_normal_completions_do_not_trigger(text):
    verdict, _, _ = classify(text, user_text="")
    assert verdict == "ok"


# ── guards (false-positive prevention) ───────────────────────────────────────


def test_destructive_context_guard():
    text = "Команда перезапишет историю. Это `git push --force` в проде. Запустить?"
    assert has_destructive_context(text) is True
    verdict, _, _ = classify(text, user_text="")
    assert verdict == "guard:destructive"


def test_destructive_ru_guard():
    text = "Это удалит таблицу users в проде. Делаю?"
    assert has_destructive_context(text) is True
    verdict, _, _ = classify(text, user_text="")
    assert verdict.startswith("guard:")


def test_business_ambiguity_guard():
    text = "Какой вариант предпочитаете — Option A или Option B?"
    assert has_business_ambiguity(text) is True
    verdict, _, _ = classify(text, user_text="")
    # The text has "которой вариант" matching ambiguity; the ask trigger is
    # also there via "Option A or Option B?" — guard must win.
    assert verdict.startswith("guard:") or verdict == "ok"


def test_user_question_guard():
    assistant = "Хотите, я сделаю подробнее?"
    user = "А зачем это вообще нужно?"
    verdict, _, _ = classify(assistant, user_text=user)
    assert verdict == "guard:user_question"


def test_destructive_keyword_alone_no_shirk_keeps_ok():
    # Destructive context without a shirk pattern is just normal text.
    text = "Развернул в prod, всё ок."
    verdict, _, _ = classify(text, user_text="")
    assert verdict == "ok"


# ── tail-only matching: shirk in the middle doesn't trigger ──────────────────


def test_shirk_phrase_in_middle_does_not_trigger():
    text = (
        "Раньше я думал — запустить ли тесты прямо сейчас. Решил запустить и "
        "запустил.\n\nИтог: 14/14 зелёные, линт чистый, typecheck без ошибок."
    )
    verdict, _, _ = classify(text, user_text="")
    assert verdict == "ok"


def test_shirk_phrase_at_end_triggers():
    text = "Итог: 14/14 тесты зелёные, линт чистый.\n\nОсталось проверить typecheck. Запустить?"
    verdict, _, _ = classify(text, user_text="")
    assert verdict == "shirk"


# ── code-block immunity ─────────────────────────────────────────────────────


def test_question_inside_fenced_block_is_ignored():
    text = "Вот что я добавил:\n\n```\nprint('Should I run the tests?')\n```\n\nГотово. 14/14."
    verdict, _, _ = classify(text, user_text="")
    assert verdict == "ok"


# ── transcript reading helpers ───────────────────────────────────────────────


def test_last_assistant_text_skips_tool_use_only(write_transcript):
    path = write_transcript(
        [
            ("user", "do the thing"),
            ("assistant", [{"type": "tool_use", "id": "x", "name": "Bash", "input": {}}]),
            ("assistant", "Готово. Запустить?"),
        ]
    )
    events = no_shirk_hook.read_transcript(path)
    assert "Запустить" in last_assistant_text(events)


def test_last_user_text_returns_latest(write_transcript):
    path = write_transcript(
        [
            ("user", "first"),
            ("assistant", "ok"),
            ("user", "second"),
        ]
    )
    events = no_shirk_hook.read_transcript(path)
    assert last_user_text(events) == "second"


# ── main() integration via stdin + transcript file ───────────────────────────


def via_main(monkeypatch, capsys, *, transcript_path, stop_hook_active=False):
    payload = json.dumps(
        {
            "session_id": "test",
            "transcript_path": transcript_path,
            "stop_hook_active": stop_hook_active,
            "hook_event_name": "Stop",
        }
    )
    monkeypatch.setattr("sys.stdin", io.StringIO(payload))
    no_shirk_hook.main()
    return capsys.readouterr().out


def test_main_blocks_shirking_turn(monkeypatch, capsys, write_transcript):
    path = write_transcript(
        [
            ("user", "почини баг"),
            ("assistant", "Готово. Тесты 14/14.\n\nОсталось typecheck. Запустить?"),
        ]
    )
    out = via_main(monkeypatch, capsys, transcript_path=path)
    decision = json.loads(out)
    assert decision["decision"] == "block"
    assert "ask-instead-of-do" in decision["reason"]


def test_main_allows_normal_completion(monkeypatch, capsys, write_transcript):
    path = write_transcript(
        [
            ("user", "почини баг"),
            ("assistant", "Готово. Тесты 14/14, линт чист, typecheck зелёный."),
        ]
    )
    out = via_main(monkeypatch, capsys, transcript_path=path)
    assert out == ""


def test_main_respects_loop_guard(monkeypatch, capsys, write_transcript):
    path = write_transcript(
        [
            ("user", "fix"),
            ("assistant", "Готово. Запустить?"),
        ]
    )
    out = via_main(monkeypatch, capsys, transcript_path=path, stop_hook_active=True)
    assert out == ""


def test_main_no_transcript_path_exits_clean(monkeypatch, capsys):
    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps({"hook_event_name": "Stop"})))
    no_shirk_hook.main()
    assert capsys.readouterr().out == ""


def test_main_empty_assistant_text_exits_clean(monkeypatch, capsys, write_transcript):
    path = write_transcript(
        [
            ("user", "fix"),
            ("assistant", [{"type": "tool_use", "id": "x", "name": "Bash", "input": {}}]),
        ]
    )
    out = via_main(monkeypatch, capsys, transcript_path=path)
    assert out == ""


def test_main_guards_destructive_question(monkeypatch, capsys, write_transcript):
    path = write_transcript(
        [
            ("user", "rollback"),
            ("assistant", "Готов сделать `git push --force` в прод. Запустить?"),
        ]
    )
    out = via_main(monkeypatch, capsys, transcript_path=path)
    assert out == ""


def test_build_reason_includes_snippet():
    reason = build_reason("Запустить?")
    assert "Запустить?" in reason
    assert "(1)" in reason
    assert "(2)" in reason
    assert "(3)" in reason


def test_build_reason_truncates_long_snippet():
    long = "x " * 200
    reason = build_reason(long)
    assert "…" in reason
