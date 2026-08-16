#!/usr/bin/env python3
"""Raw Google SERP lookup for the `investigate` skill.

Perplexity and WebSearch return synthesized answers. Sometimes the evidence *is* the
result list itself — what a query returns, in what order, from which domains. That is what
this fetches, and the only reason to reach for it.

Usage:
    python3 serp_search.py --check          # is SERPAPI_API_KEY present?
    python3 serp_search.py "query" [n]      # top n results (default 10)
"""

from __future__ import annotations

import json
import os
import sys
import urllib.parse
import urllib.request

_ENDPOINT = "https://serpapi.com/search.json"
_TIMEOUT_S = 30


def _api_key() -> str:
    """Return the SerpAPI key, or exit with an explanation of how to get one."""
    key = os.environ.get("SERPAPI_API_KEY")
    if not key:
        sys.stderr.write(
            "SERPAPI_API_KEY is not set. This tool is optional — use Perplexity MCP or "
            "WebSearch instead, or export a key from https://serpapi.com/manage-api-key\n",
        )
        raise SystemExit(2)
    return key


def search(query: str, num: int) -> list[str]:
    """Return the organic results for `query`, one rendered block per result."""
    params = urllib.parse.urlencode({"engine": "google", "q": query, "num": num, "api_key": _api_key()})
    request = urllib.request.Request(f"{_ENDPOINT}?{params}", method="GET")  # noqa: S310  # literal https endpoint
    with urllib.request.urlopen(request, timeout=_TIMEOUT_S) as response:  # noqa: S310  # same
        payload = json.loads(response.read())
    return [format_result(rank, hit) for rank, hit in enumerate(payload["organic_results"], start=1)]


def format_result(rank: int, hit: dict[str, str]) -> str:
    """Render one organic result as rank, title, link, snippet — SerpAPI omits `snippet` on some types."""
    return f"{rank}. {hit['title']}\n   {hit['link']}\n   {hit.get('snippet', '')}"


def main() -> None:
    """Run --check, or search for argv[1] and write the results to stdout."""
    args = sys.argv[1:]
    if not args:
        sys.stderr.write(__doc__ or "")
        raise SystemExit(2)
    if args[0] == "--check":
        sys.stdout.write(
            "SERPAPI_API_KEY present\n" if os.environ.get("SERPAPI_API_KEY") else "SERPAPI_API_KEY absent\n"
        )
        return
    results = search(args[0], int(args[1]) if len(args) > 1 else 10)
    sys.stdout.write("\n".join(results) + "\n" if results else "no organic results\n")


if __name__ == "__main__":
    main()
