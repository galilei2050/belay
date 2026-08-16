# Worked trace

One real investigation, compressed. Read it for the shape: what a leaf looks like once it
carries a number, and what "the arithmetic closed" actually means.

**Question.** Cost per lead reported $28 → $76 over a year, while ad targeting had measurably
improved. 15 hypotheses across 4 branches, 5 evidence dispatches, 4 correlation scripts.

**Tree at the end** (abridged):

```
Q0: Why did reported CPL grow ×2.69?
├── Q1: Is the measurement wrong?
│   ├── H1.1: numerator is paid-only spend, denominator mixes paid + organic
│   │         phones                                          [VERIFIED] explains ×1.74
│   └── H1.2: duplicate-call dedup broken                      [FALSIFIED] dedup correct, 0.4% dupes
├── Q2: Did the input change?
│   └── H2.1: lead volume fell                                 [PARTIAL] −18%, inside H1.1's population
├── Q3: Did the system change?
│   └── H3.1: campaign shut off mid-year                       [VERIFIED] −50% paid call volume
└── Q4: Outside world?
    └── H4.1: bought reviews penalized by the platform         [FALSIFIED] by a user-stated fact (Step 8)
```

**What each step actually caught:**

- *Step 1* found H1.1 — the ratio's two sides had different scopes. Reading the compute
  function, not the dashboard, is what surfaced it.
- *Step 5b* dated H3.1 to a specific shutdown in the changelog. Without that date it was a
  correlation, not a cause.
- *Step 7* closed: real cost per **ad** lead was ×1.55; the remaining ×1.74 was the scope
  artifact. ×1.55 × ×1.74 ≈ ×2.69, the observed number. Nothing was left over.
- *Step 8* fired mid-investigation: the user stated the reviews were from real customers, so
  "platform penalized us" flipped to `[FALSIFIED]` and the actual null lift became the
  evidence instead.
- *quantitative.md* mattered twice: one contaminated point in a 12-month series flipped lag-1
  `r` from +0.36 to −0.097, and a raw `r = −0.35` fell to −0.11 partial once spend was
  controlled for. Both would have been reported as real effects.

**The fix that followed** was not "reduce CPL": the muddy metric was split into a narrow
paid-scope measure and a fully blended one, because averaging two scopes had produced a
number nobody could act on.
