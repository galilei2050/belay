"""Drive the script the way an investigating agent does: argv in, stdout out."""

import io
import json

import pytest
import serp_search

# Shape copied from a real SerpAPI response: `position` alongside the rank we render, and a
# result type that carries no `snippet` at all.
_PAYLOAD = {
    "organic_results": [
        {"position": 1, "title": "Why CPL doubled", "link": "https://e.com/a", "snippet": "mixed denominator"},
        {"position": 2, "title": "No snippet here", "link": "https://e.com/b", "displayed_link": "e.com"},
    ]
}


@pytest.fixture
def serpapi(monkeypatch):
    """Answer the SerpAPI call with `payload` and capture the URL the script built."""
    calls = []

    def install(payload):
        def fake_urlopen(request, timeout):  # noqa: ARG001
            calls.append(request.full_url)
            return io.BytesIO(json.dumps(payload).encode())

        monkeypatch.setattr(serp_search.urllib.request, "urlopen", fake_urlopen)
        return calls

    return install


@pytest.fixture
def run(monkeypatch):
    """Run main() with the given argv."""

    def go(*argv: str):
        monkeypatch.setattr(serp_search.sys, "argv", ["serp_search.py", *argv])
        serp_search.main()

    return go


def test_results_are_rendered_ranked_with_link_and_snippet(monkeypatch, serpapi, run, capsys):
    monkeypatch.setenv("SERPAPI_API_KEY", "abc123")
    serpapi(_PAYLOAD)
    run("why did cpl double")
    assert capsys.readouterr().out == (
        "1. Why CPL doubled\n   https://e.com/a\n   mixed denominator\n2. No snippet here\n   https://e.com/b\n   \n"
    )


def test_the_query_and_key_reach_serpapi(monkeypatch, serpapi, run, capsys):
    monkeypatch.setenv("SERPAPI_API_KEY", "abc123")
    calls = serpapi(_PAYLOAD)
    run("cpl doubled")
    capsys.readouterr()
    assert calls == ["https://serpapi.com/search.json?engine=google&q=cpl+doubled&num=10&api_key=abc123"]


def test_zero_hits_say_so_instead_of_crashing(monkeypatch, serpapi, run, capsys):
    monkeypatch.setenv("SERPAPI_API_KEY", "abc123")
    serpapi({"search_information": {"organic_results_state": "Fully empty"}})
    run("zzqx nonsense query")
    assert capsys.readouterr().out == "no organic results\n"


def test_an_error_blob_is_reported_verbatim(monkeypatch, serpapi, run):
    monkeypatch.setenv("SERPAPI_API_KEY", "abc123")
    serpapi({"error": "Your account credits are exhausted."})
    with pytest.raises(SystemExit) as exc:
        run("anything")
    assert exc.value.code == "SerpAPI: Your account credits are exhausted."


def test_missing_key_exits_two_and_points_at_the_signup(monkeypatch, run, capsys):
    monkeypatch.delenv("SERPAPI_API_KEY", raising=False)
    with pytest.raises(SystemExit) as exc:
        run("anything")
    assert exc.value.code == 2
    assert "serpapi.com/manage-api-key" in capsys.readouterr().err


def test_no_query_prints_usage_and_exits_two(run, capsys):
    with pytest.raises(SystemExit) as exc:
        run()
    assert exc.value.code == 2
    assert 'serp_search.py "query"' in capsys.readouterr().err
