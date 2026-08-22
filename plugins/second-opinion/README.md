# second-opinion

Ask Gemini. It has not read this session, has not agreed with anything in it, and cannot be
nudged by a plan it helped write — which is the whole reason to ask it.

## What it does

| Component | What it is |
|---|---|
| `scripts/ask_gemini.py` | One question in, one answer out, through Vertex AI |
| `skills/second-opinion` | When asking is worth it, how to phrase the question, what to do with the answer |
| `/second-opinion:ask <question>` | The same thing by hand |

```bash
python3 plugins/second-opinion/scripts/ask_gemini.py "which of these two, and what would change your mind?" \
    --file app/cache.py --file app/queue.py
```

- `--file` inlines a file, repeatable. The model cannot open paths on this machine.
- The model is **not pinned**: Vertex is asked what it lists, and the newest Pro that actually
  answers is the one used. A name written into the source is a name that is a generation behind
  by the time anyone reads it. `--model` pins one anyway; `--show-model` says which answered.
- The endpoint is `global`, which Google routes to wherever a model is served — a fixed region
  lists an older catalogue (measured: `gemini-3.1-pro-preview` is reachable on `global` and
  absent from `us-central1` for this project).

Failures are loud and name the fix: a 404 prints the model name Vertex refused, a blocked or
empty answer exits non-zero instead of printing nothing (an empty answer read as agreement is
worse than an error), and a missing login says which command to run.

## Install

```
/plugin install second-opinion@belay
```

Requires `gcloud` Application Default Credentials (`gcloud auth application-default login`) and a
project with Vertex AI enabled. `google-auth` comes with the Google Cloud SDK; if you use a token
from elsewhere, export `SECOND_OPINION_ACCESS_TOKEN` and neither is needed.

## Config

None. Auth, project and region are the ones `gcloud` already has:

| Variable | Meaning |
|---|---|
| `GOOGLE_CLOUD_PROJECT` | project billed for the call — required |
| `SECOND_OPINION_ACCESS_TOKEN` | use this bearer token instead of ADC |
| `SECOND_OPINION_BASE_URL` | send the call somewhere else entirely |

Check it works in one line — it prints the model it chose and the answer:

```bash
python3 plugins/second-opinion/scripts/ask_gemini.py --show-model "reply with the single word: ok"
```

## The one rule that is not about plumbing

Nothing secret goes in a question — it leaves the machine for a third party. And the answer is an
argument, not an authority: Gemini is guessing at any code you did not paste it, so check the
factual claims before acting on them, and when it disagrees with you, report the disagreement
rather than quietly picking a side.
