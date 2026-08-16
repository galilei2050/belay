#!/usr/bin/env python3
"""Raw Google SERP lookup for the `investigate` skill.

Perplexity and WebSearch return synthesized answers. Sometimes the evidence *is* the
result list itself — what a query returns, in what order, from which domains. That is what
this fetches, and the only reason to reach for it.

Usage:
    python3 serp_search.py "query"
"""

from __future__ import annotations

import json
import os
import sys
import urllib.parse
import urllib.request

_ENDPOINT = "https://serpapi.com/search.json"
_RESULTS = 10
_TIMEOUT_S = 30


def _api_key() -> str:
    """Exit with a pointer rather than searching anonymously, which SerpAPI answers with an error blob."""
    key = os.environ.get("SERPAPI_API_KEY")
    if not key:
        sys.stderr.write(
            "SERPAPI_API_KEY is not set. This tool is optional — use Perplexity MCP or "
            "WebSearch instead, or export a key from https://serpapi.com/manage-api-key\n",
        )
        raise SystemExit(2)
    return key


def search(query: str) -> list[str]:
    """Return the organic results for `query`, one rendered block per result."""
    params = urllib.parse.urlencode({"engine": "google", "q": query, "num": _RESULTS, "api_key": _api_key()})
    request = urllib.request.Request(f"{_ENDPOINT}?{params}", method="GET")  # noqa: S310  # literal https endpoint
    with urllib.request.urlopen(request, timeout=_TIMEOUT_S) as response:  # noqa: S310  # same
        payload = json.loads(response.read())
    # SerpAPI answers a bad key, an exhausted quota and a zero-hit query alike: HTTP 200, an
    # `error` string, and no `organic_results` at all. Without this the three arrive as one
    # indistinguishable KeyError.
    if "error" in payload:
        raise SystemExit(f"SerpAPI: {payload['error']}")
    return [format_result(rank, hit) for rank, hit in enumerate(payload.get("organic_results", []), start=1)]


def format_result(rank: int, hit: dict[str, str]) -> str:
    """SerpAPI omits `snippet` on some result types."""
    return f"{rank}. {hit['title']}\n   {hit['link']}\n   {hit.get('snippet', '')}"


def main() -> None:
    """Search for argv[1] and write the rendered results to stdout."""
    args = sys.argv[1:]
    if not args:
        sys.stderr.write(__doc__ or "")
        raise SystemExit(2)
    results = search(args[0])
    sys.stdout.write("\n".join(results) + "\n" if results else "no organic results\n")


if __name__ == "__main__":
    main()
