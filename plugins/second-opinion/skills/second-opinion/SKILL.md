---
name: second-opinion
description: Puts a question to Gemini — a model outside this session — and brings the answer back. Use when a decision has two defensible options and no data separates them, when a diagnosis has survived only your own reasoning, when a design is about to be built and nobody has argued against it, or when the user asks what another model thinks ("спроси Gemini", "second opinion", "посоветуйся"). NOT for facts you can check yourself with a grep, a query or a test — those are cheaper and actually authoritative.
---

# Second opinion

**Gemini has not read this session.** That is the entire value: it has not agreed with anything
here, it does not know which option you already started implementing, and it cannot be nudged by
the phrasing of a plan it helped write. It is also why the question has to carry its own context —
what it is not told, it will invent around.

Everything else about it is a liability: it cannot run your tests, read your repo, or see the
error you are looking at. It answers from what you type and from what it was trained on.

## When it is worth asking

- **A fork with two defensible options** and no measurement that separates them — the classic
  case, because a second reading of the same trade-offs is exactly what you lack.
- **A diagnosis that only you have argued for.** State the evidence, ask what else produces it.
- **A design nobody has attacked.** Ask for the strongest objection, not for approval.
- **A stuck bug** where you have read the same code four times.
- The user asked. Do not overrule that with "I could work it out myself".

## When not to ask

- The answer is in the repo, the database, the logs or a test run. Go get it — Gemini's guess
  about your code is worth less than one `grep`, and asking anyway is how a checkable fact turns
  into a hedge. (`rules/ground-claims-in-data.md`)
- You want reassurance. A model asked "is this right?" tends to say yes; you learn nothing.
- The decision is the user's to make — business rules, priorities, what to build. Ask *them*.

## How to ask

```bash
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/ask_gemini.py" "<question>" --file path/to/relevant.py
```

- `--file` inlines a file as context, repeatable. The model cannot open paths on this machine, so
  a path without `--file` is a path it will guess about.
- The model is chosen from Vertex's own list — the newest Pro that answers. Do not pass `--model`
  unless the user asked for a specific one; a name you remember is older than the one on the list.
- Auth is the `gcloud` login already on the machine — nothing to configure, nothing stored.

**Write the question the way you would write it to a senior colleague who just walked in:**

1. What you are building, in one sentence.
2. The constraint that makes the obvious answer wrong.
3. The options you see, and what you already tried.
4. The precise question — "which of these two, and what would change your mind?" beats "what do
   you think?"
5. Ask for the objection: "what breaks first?", "what am I not seeing?"

**Never paste secrets.** `.env`, tokens, keys, customer data — this leaves the machine and goes to
a third party. Strip or summarise. If the question cannot be asked without a secret, it cannot be
asked.

## What to do with the answer

It is an opinion from a model that cannot see your system — treat it exactly as strong as its
reasoning, never as authority.

- **Check every factual claim it makes about your code.** It is guessing at anything you did not
  paste; a confident wrong claim about your repo reads identically to a right one.
- **Take the objection, not the conclusion.** The useful part is usually the failure mode it
  names, not the option it picks.
- **When it disagrees with you, say so to the user** — the disagreement is the deliverable. Report
  what it argued and where you still differ, rather than quietly switching sides or quietly
  ignoring it.
- **Do not launder it into fact.** "Gemini suggests X because Y" is honest; "X is the standard
  approach" is not, unless you checked.
- One round is usually enough. If you find yourself on the third exchange, the question was
  under-specified — rewrite it rather than trading paragraphs.
