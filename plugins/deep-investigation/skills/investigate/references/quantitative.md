# Quantitative evidence

Read this before computing any relationship between two series. Every rule here exists
because skipping it produces a confident answer with the wrong sign.

## Raw correlation between two business series is meaningless on its own

When a third variable drives both, `r(A, B)` measures the third variable. Compute the
**partial correlation** controlling for the obvious common driver (spend, traffic, volume,
or time itself) before you call a relationship real.

Worked case: raw `r(reviews, CPL) = −0.35` same-month looks like a real effect. Partial `r`
controlling for ad spend: `−0.11` — spurious, spend moved both. Meanwhile raw
`r(reviews_{t−1}, CPL_t) = −0.82` held at `−0.80` partial — that one is real.

Always compute partial `r` when two levers co-move with the outcome, or when a time trend
exists — which is nearly always. Control for time or use spend as its proxy.

## Test lags, and report all of them

Anything behavioral, marketing-driven, or indexed by a third party arrives late: brand
awareness, ranking updates, conversion timing, seasonal shifts. Test lag-0 through lag-3
minimum, and write down every lag you tested, not only the winner.

Same case: spend → maps-listing halo was `r = +0.26` same-month and `+0.71` at lag-3.
Reporting only the same-month number would have concluded "no effect".

## One outlier flips a short series

On a 12-point series a single contaminated point can reverse the sign. Before computing
anything on n < 24:

1. Identify known non-organic events first — one-off campaigns, backfills, incentivized
   spikes, migrations. Do this from the changelog, not from the shape of the data.
2. Compute with all points, then without the identified outlier, and report both.
3. Do not drop more than two points without writing down why.
4. Flag `n < 15` results as "direction indicated, magnitude uncertain" — never as "strong".

## Correlation earns the word "mechanism" only from the changelog

After a leaf reaches `[VERIFIED]` statistically, search the business/engineering changelog
— git log, deploy history, incident channel, ops docs, Notion — for a recorded decision at
that date that would produce this pattern. Statistics gives you a consistent hypothesis;
the changelog entry converts it into a cause.

If nothing is recorded, say so explicitly. An unrecorded change is itself a finding
(undocumented intervention, unplanned drift) — much better than inventing a mechanism to
fill the gap. Some patterns are structural (definition, attribution window) and correctly
have no changelog entry at all.

## Sanity checks that catch fabricated analysis

- State `n` next to every `r`. An `r` without an `n` is not a result.
- A relationship that only exists in one slicing and disappears in the others is noise.
- If the effect size cannot be expressed in the unit of the original question (dollars,
  requests, points of the metric), it has not been measured yet.
