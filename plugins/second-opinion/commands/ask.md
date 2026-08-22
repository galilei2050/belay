---
description: Ask Gemini for a second opinion on the question in $ARGUMENTS
---

Put `$ARGUMENTS` to Gemini and bring the answer back, following the `second-opinion:second-opinion`
skill for how to phrase it.

1. Write the question so it stands on its own: what is being built, the constraint that rules out
   the obvious answer, the options, the precise thing you want decided. Gemini has not read this
   session and cannot open files on this machine.
2. Attach the files it needs with `--file`, one flag each. Strip secrets first.
3. Run it:

```bash
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/ask_gemini.py" "<question>" --file <path>
```

4. Report back: what it argued, which factual claims about our code you verified, and where you
   still disagree. The disagreement is the useful part — do not flatten it into consensus.

If `$ARGUMENTS` is empty, ask the user what they want a second opinion on rather than inventing a
question.
