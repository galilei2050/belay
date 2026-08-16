import pytest
from serp_search import _api_key, format_result


def test_missing_key_exits_loudly(monkeypatch):
    monkeypatch.delenv("SERPAPI_API_KEY", raising=False)
    with pytest.raises(SystemExit) as exc:
        _api_key()
    assert exc.value.code == 2


def test_key_is_returned_when_set(monkeypatch):
    monkeypatch.setenv("SERPAPI_API_KEY", "abc123")
    assert _api_key() == "abc123"


def test_format_result_renders_rank_title_link_snippet():
    hit = {"title": "Why CPL doubled", "link": "https://example.com/a", "snippet": "mixed-source denominator"}
    assert format_result(3, hit) == "3. Why CPL doubled\n   https://example.com/a\n   mixed-source denominator"


def test_format_result_survives_a_result_without_a_snippet():
    assert format_result(1, {"title": "T", "link": "https://e.com"}) == "1. T\n   https://e.com\n   "
