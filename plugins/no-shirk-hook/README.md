# no-shirk-hook

Stop hook that blocks the agent from ending a turn with an ask-instead-of-do
question.

## What it does

After every main-agent turn (`Stop` event), the hook takes the turn's final
assistant message from the payload's `last_assistant_message` (the transcript
file can lag behind it). A closing «Источники:»/"Sources:" paragraph is
skipped, and trailing `result:`/`failed:`/`needs input:` status lines are
checked together with the paragraph above them.

Separately, the whole message is scanned for sending the user to ask a third
party («нужно спросить у Yelp», "check with the vendor") — the `offload_check`
group, which gets its own reason: research it yourself.

If the closing paragraph matches a *shirking* pattern — e.g.
«Запустить?», «Хочешь, я починю?», "Should I run the tests?", "Want me to
continue?" — and no false-positive guard fires, it returns:

```json
{"decision": "block", "reason": "Ты закончил ход вопросом …"}
```

Claude Code then re-enters the agent and forces it to *do* the next obvious
step instead of asking. The `reason` spells out exactly when asking IS
legitimate, so the agent learns the rule, not just the verdict.

## When the hook does NOT block (false-positive guards)

The hook deliberately stays out of the way when stopping to ask is the right
move:

1. **Destructive context** — split by reversibility:
   - *Hard* (`--force`, `drop table`, `rm -rf`, `удалить`, …) — irreversible,
     always confirmed.
   - *Soft deploy-ish* (`deploy`, `prod`, `release`, `migration`, `выкладк…`) —
     confirmed only when the turn offers no reversible action. "deploy to prod?"
     stays guarded; "commit, push, open PR (then it deploys)?" does not — the
     reversible part is just done.
   A read-only "want me to check/look?" offer is **not** excused by this guard —
   looking is never the destructive act, so it's still blocked.
2. **Business ambiguity** — the tail offers a tradeoff or two named
   alternatives («какой вариант», "which approach", "A or B?"). Business
   decisions belong to the human.
3. **User asked an open question** — the previous user message itself ends
   with `?` and is short. Then the agent is answering, not shirking. Doesn't
   excuse `offload_check`: "ask the vendor" is no answer to a question.

For an `offload_check` hit, guards 1 and 2 read the paragraph the phrase sits
in, not the tail.
4. **Loop guard** — if `stop_hook_active` is already true, the hook does
   nothing, so two blocks can't ping-pong.

It also ignores shirking phrases that sit inside fenced code blocks, inline
code, or quoted (`>`) lines.

## Install

```
/plugin marketplace add galilei2050/belay
/plugin install no-shirk-hook@belay
```

Pure stdlib — no `pip install` needed.

## Tune

Patterns and guards live as Python lists at the top of
`hooks/no_shirk_hook.py`. Add a new RU/EN phrase pair, write one positive and
one negative test in `tests/test_no_shirk_hook.py`, run `pytest plugins/`. If
the hook starts blocking legitimate turns, extend `HARD_DESTRUCTIVE_KEYWORDS` /
`SOFT_DEPLOY_KEYWORDS` or `BUSINESS_AMBIGUITY_MARKERS` rather than weakening the
patterns. A false `offload_check` hit is the exception: narrow
`OFFLOAD_CHECK_PATTERNS` itself, since the guards only see the hit's paragraph.

Logs: `~/.claude/logs/no-shirk-hook.log`.
