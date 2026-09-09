# Policy Validation Methodology

## What this measures

Not the skill. The **policy** — `scripts/policy.json` plus the `scripts/modes/*.json` overlays.

The skill's pipeline (subagent search → score → main agent reads) is assumed to work; it is
plumbing. The open question is whether the scoring policy's verdicts correspond to anything
real. A tier table is a set of assertions about the world ("nature.com is more trustworthy
than a personal blog"). Those assertions can be wrong, can be stale, and can encode a bias
nobody chose. This procedure tests them against live search results.

## The core constraint: independence

A source's quality judgment MUST be produced without the judge seeing the policy's score.
If the same agent scores and then judges, it will rationalize the score, and the policy ends
up grading its own exam. Every measurement below depends on this separation holding.

Concretely: the judge agent has no access to `srcscore.py`, `policy.json`, the tier table, or
the verdict names. It is told only the research question and asked whether each source would
actually help answer it.

## Procedure (per question)

1. **Judge** (subagent, Sonnet, no policy access)
   - Plain web search for the question. Collect 40-60 URLs.
   - For each URL, assign `useful` / `marginal` / `junk` against the question, plus:
     - `vendor_interest`: does the publisher sell the thing the question is about?
     - `basis`: `fetched` (opened the page) or `snippet` (judged from title+snippet).
       Snippet-basis is allowed only for obvious cases and is recorded so it can be discounted.
   - Writes `policy-eval/runs/<id>.judge.json`. Nothing else.

   **Two rounds, for `expansion: true` questions only** (`ac6`, `na6`, `co6`, `nw6`, `od6`):

   1. Round 1 — plain web search for the question as written. Collect URLs.
   2. Derive keywords — from the **body text of round-1 sources the judge actually opened**,
      take the terms the question needs to reach a part of itself that is still unresolved, and
      record each with the URL it came from in `expansion_keywords`. Body text, because that is
      where the skill's expansion comes from too: the main agent finds the term while reading a
      source that passed and hands it to a new subagent (`SKILL.md` Step 3). A keyword may only
      be derived from an entry whose `basis` is `fetched` — a title is not a source for one.
      The judge still never sees the policy.
   3. Round 2 — search those keywords, collect more URLs.
   4. Label the merged pool by the same criteria as above, with `round: 1|2` on every entry.
      `rank` is the rank within its own round.

   Schema additions: top-level `expansion_keywords` (`{keyword, source_url}`) and per-entry
   `round`. Both optional — the five pilot files stay valid and are read as one round.

   These runs therefore need a real fetch rate on round 1 to mean anything, which makes them the
   first place to apply the judge-fetch changes in TODO.md B rather than the last.

2. **Score** (deterministic, 0 tokens)
   - `srcscore.py --in <urls> --mode <mode> --field <field> --format json`
   - Writes `policy-eval/runs/<id>.score.json`.

3. **Contrast** (mechanical join on URL)
   - Writes `policy-eval/runs/<id>.contrast.json`.

## Measures

Treat `useful` as positive, `junk` as negative, `marginal` as excluded from
precision/recall (reported separately — a filter that keeps marginals is not obviously wrong).
The policy's positive set is PRIMARY+SUPPORT; negative is WEAK+DROP+BLOCKED; SKIM is the
policy's own "marginal" and is likewise reported apart.

- **Recall loss (the important one)**: `useful` sources scored below SUPPORT.
  These are the sources the skill would never have opened. This is the cost of filtering
  and the number most likely to be embarrassing.
- **Precision loss**: `junk` sources scored PRIMARY or SUPPORT.
- **Vendor displacement**: rank of vendor-interest sources under the policy vs. under
  plain search order. This is the mechanism the harness-tool anecdote suggested; this
  measures whether it reproduces.
- **Tier attribution**: for every disagreement, which policy component caused it —
  domain tier, citation count, recency decay, engagement, or a penalty. Tells us what to
  fix rather than that something is broken.

For the two-round questions, everything above is also reported **split by round**: how many
`useful` sources round 2 contributed, the recall loss over round-2 URLs alone, and how many
round-2 URLs landed on unregistered tier-5 domains. The last one is the direct measurement of
the F1 interaction — expansion surfaces more unregistered domains, so better search makes
domain coverage matter more, not less.

## What this cannot show

- Whether reports written from filtered sources are more *accurate*. That needs claim-level
  verification against ground truth and is out of scope here.
- Statistical significance. n=5 pilot, n=30 full (25 one-shot + 5 two-round). These are case studies with a consistent
  procedure, not a benchmark. Report them as such.
- Judge reliability. One Sonnet judge per question, no second rater, so there is no
  inter-rater agreement figure. Disagreements that hinge on a debatable `useful`/`marginal`
  call should be flagged rather than counted.

## Pilot (n=5)

One question per mode, each chosen as the mode's hardest case:
`na2` (vendor trap), `co2` (short half-life vs. old-is-better), `od3` (recency-off vs.
version rot), `nw1` (primary vs. re-report), `ac2` (pharma vendor interest).

Pilot exit criteria before spending on the remaining 25:
- Judge output is parseable and the useful/junk split is not degenerate (not >90% one label).
- At least one disagreement per question is substantive, not a judge error.
- `basis: fetched` covers a majority of judgments.
