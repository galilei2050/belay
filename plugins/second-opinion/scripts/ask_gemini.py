#!/usr/bin/env python3
"""Ask Gemini a question through Vertex AI and print the answer.

The point is a *second* opinion: a model that has not read this session's context, has not
agreed with anything said so far, and will not converge on the first answer just because it is
already written down. Everything it needs has to be in the question — which is also why the
question is worth writing carefully.

Auth is whatever `gcloud` already has: Application Default Credentials and the project from the
environment. No key of its own, nothing stored, nothing to rotate.

Two things are decided by asking Vertex rather than by configuration, because both go stale:
the region (`global`, which Google routes to wherever a model is served) and the model — the
newest Pro on Vertex's own list, stepping down a generation if this project cannot call it.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import urllib.error
import urllib.request
from pathlib import Path
from typing import NamedTuple, TypedDict

# `global` is served from wherever the model actually lives, so it reaches generations that a
# single region has not picked up yet. Not configurable: a pinned region is how you end up asking
# last year's model without noticing.
REGION = "global"
TIMEOUT_S = 180

# The escape hatches, read from the environment so nothing is stored in the repo: a ready access
# token (CI, or a machine without gcloud) and a base URL (a proxy, or a local stand-in).
BEARER_FROM_ENV = "SECOND_OPINION_ACCESS_TOKEN"
BASE_URL_ENV = "SECOND_OPINION_BASE_URL"

# `gemini-<major>[.<minor>]-pro[-<suffix>]`. Pro only: this is the model you ask when the
# reasoning is the product, and a Flash answer to a design question is not the same thing.
# The suffix carries `preview` or a date — kept, because a preview Pro is still a newer Pro.
_PRO_RE = re.compile(r"^gemini-(\d+)(?:\.(\d+))?-pro(?:-(.+))?$")
# Every one of these is a different product wearing the Pro name: image generation, speech,
# screen control. They answer, and what comes back is not an opinion.
_NOT_AN_ADVISOR = ("image", "tts", "computer-use", "live", "native-audio")

# Vertex lists models a project cannot call — `gemini-3.7-flash` is listed for this account and
# answers 404 — so the newest name is a candidate, not an answer. A few tries cross a generation
# that has not rolled out; more than that is a broken setup, not a rollout.
MAX_CANDIDATES = 4

_NO_PROJECT = "GOOGLE_CLOUD_PROJECT is not set, so there is no Vertex AI project to ask. Export it, or pass --project."
_NO_CREDENTIALS = (
    "No Application Default Credentials. Run `gcloud auth application-default login` in your own "
    "terminal (it needs a browser), or export {token} with a token you already have."
)


class Candidate(TypedDict, total=False):
    """One answer in a generateContent response — `total=False` because a blocked one carries neither key."""

    content: dict[str, object]
    finishReason: str


class Answer(TypedDict, total=False):
    """The slice of the generateContent response this script reads."""

    candidates: list[Candidate]


class ProModel(NamedTuple):
    """A Pro model, ordered newest generation first and stable ahead of preview within one.

    `stable` is its own field because the fallback would otherwise be the name, and by name
    `gemini-3.1-pro-preview` sorts above `gemini-3.1-pro` — the preview would win over the
    release it is a preview of.
    """

    major: int
    minor: int
    stable: int
    name: str


def base_url() -> str:
    """The Vertex host — overridable only so the tests can point it at a local server."""
    return os.environ.get(BASE_URL_ENV) or "https://aiplatform.googleapis.com"


def access_token() -> str:
    """A bearer token: the environment's if it has one, otherwise whatever gcloud already holds."""
    token = os.environ.get(BEARER_FROM_ENV)
    if token:
        return token
    try:
        import google.auth  # noqa: PLC0415 — optional at import time: the token env var skips it
        import google.auth.transport.requests  # noqa: PLC0415
    except ImportError:
        raise SystemExit(_NO_CREDENTIALS.format(token=BEARER_FROM_ENV)) from None
    try:
        credentials, _ = google.auth.default(scopes=["https://www.googleapis.com/auth/cloud-platform"])
        credentials.refresh(google.auth.transport.requests.Request())
    except Exception as exc:  # noqa: BLE001 — google.auth raises several types for "not logged in"
        raise SystemExit(f"{_NO_CREDENTIALS.format(token=BEARER_FROM_ENV)}\n({type(exc).__name__}: {exc})") from None
    return str(credentials.token)


def _request(url: str, project: str, *, data: bytes | None = None) -> dict[str, object]:
    """One authenticated Vertex call. `x-goog-user-project` is what makes ADC usable for listing."""
    headers = {
        "Authorization": f"Bearer {access_token()}",
        "Content-Type": "application/json",
        "x-goog-user-project": project,
    }
    request = urllib.request.Request(url, data=data, headers=headers)  # noqa: S310 — https, built from config
    with urllib.request.urlopen(request, timeout=TIMEOUT_S) as response:  # noqa: S310
        return json.loads(response.read())


def pro_models(project: str) -> list[str]:
    """Every Pro text model Vertex offers here, newest generation first."""
    listing = _request(f"{base_url()}/v1beta1/publishers/google/models?pageSize=300", project)
    published = listing.get("publisherModels")
    items = published if isinstance(published, list) else []
    names = [str(item.get("name", "")).split("/")[-1] for item in items if isinstance(item, dict)]
    ranked = []
    for name in names:
        match = _PRO_RE.match(name)
        if match is None:
            continue
        major, minor, suffix = match.groups()
        if suffix and any(word in suffix for word in _NOT_AN_ADVISOR):
            continue
        stable = 0 if suffix and any(word in suffix for word in ("preview", "exp")) else 1
        ranked.append(ProModel(int(major), int(minor or 0), stable, name))
    if not ranked:
        raise SystemExit(f"Vertex lists no Gemini Pro model for {project}. Pass --model explicitly.")
    return [model.name for model in sorted(ranked, reverse=True)[:MAX_CANDIDATES]]


def build_prompt(question: str, files: list[Path]) -> str:
    """The question, with each context file inlined under its own path heading.

    Inlined rather than attached because the model has no access to this machine: a path it
    cannot open is worse than no context at all, since the answer will be about the wrong file.
    """
    if not files:
        return question
    blocks = [f"--- {path} ---\n{path.read_text()}" for path in files]
    return "\n\n".join([question, "Context files:", *blocks])


def answer_text(body: dict[str, object]) -> str:
    """The answer out of a generateContent response.

    A response with no candidate is an answer that was blocked or empty, and printing "" would
    read as agreement. Say which, and let the caller see the reason.
    """
    listed = body.get("candidates")
    candidates: list[Candidate] = listed if isinstance(listed, list) else []
    if not candidates:
        raise SystemExit(f"Gemini returned no candidate — response was: {json.dumps(body)[:500]}")
    parts = (candidates[0].get("content") or {}).get("parts")
    pieces = [part.get("text", "") for part in parts if isinstance(part, dict)] if isinstance(parts, list) else []
    text = "".join(pieces).strip()
    if not text:
        finish = candidates[0].get("finishReason", "unknown")
        raise SystemExit(f"Gemini returned an empty answer (finishReason={finish}).")
    return text


def ask(question: str, project: str, model: str, files: list[Path]) -> str:
    """Put the question to one model and return its answer."""
    url = f"{base_url()}/v1/projects/{project}/locations/{REGION}/publishers/google/models/{model}:generateContent"
    payload = json.dumps({"contents": [{"role": "user", "parts": [{"text": build_prompt(question, files)}]}]}).encode()
    try:
        body = _request(url, project, data=payload)
    except urllib.error.HTTPError as exc:
        detail = exc.read()[:500].decode(errors="replace")
        raise SystemExit(f"Vertex AI answered {exc.code} {exc.reason} for model {model}:\n{detail}") from None
    return answer_text(body)


def ask_newest_pro(question: str, project: str, files: list[Path], *, verbose: bool) -> str:
    """Ask the newest Pro that actually answers.

    A 404 means the catalogue offers a model this project cannot call — step down a generation.
    Any other status is a real failure (quota, auth, a bad request) and is not retried: walking
    the list would just repeat it under a different model name.
    """
    candidates = pro_models(project)
    for index, model in enumerate(candidates):
        if verbose:
            sys.stderr.write(f"[second-opinion] asking {model}\n")
        try:
            return ask(question, project, model, files)
        except SystemExit as exit_reason:
            if "404" not in str(exit_reason) or index + 1 == len(candidates):
                raise
            sys.stderr.write(f"[second-opinion] {model} is listed but not reachable here; stepping down\n")
    raise SystemExit(f"None of the Pro models Vertex lists answered: {', '.join(candidates)}")


def main() -> None:
    """CLI entry point: one question in, one answer out."""
    parser = argparse.ArgumentParser(description="Ask Gemini for a second opinion.")
    parser.add_argument("question", nargs="*", help="the question; omit to read it from stdin")
    parser.add_argument("--file", type=Path, action="append", default=[], help="file to include as context")
    parser.add_argument("--model", default=None, help="pin a model instead of taking Vertex's newest Pro")
    parser.add_argument("--project", default=os.environ.get("GOOGLE_CLOUD_PROJECT"))
    parser.add_argument("--show-model", action="store_true", help="name the model on stderr before asking")
    args = parser.parse_args()

    question = " ".join(args.question).strip() or sys.stdin.read().strip()
    if not question:
        raise SystemExit("No question given (argument or stdin).")
    if not args.project:
        raise SystemExit(_NO_PROJECT)

    if args.model:
        sys.stdout.write(ask(question, args.project, args.model, args.file) + "\n")
        return
    sys.stdout.write(ask_newest_pro(question, args.project, args.file, verbose=args.show_model) + "\n")


if __name__ == "__main__":
    main()
