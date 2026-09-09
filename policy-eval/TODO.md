# Policy validation — next steps

State as of the n=5 pilot. Read `METHODOLOGY.md` first, then `PILOT.md` for findings.
`questions.json` holds all 25 questions (5 modes x 5); only 5 have been run.

Tracked: `METHODOLOGY.md`, `PILOT.md`, `questions.json`, `contrast.py`, this file.
Untracked (`.gitignore`): `runs/` — raw judge output, URL lists, contrast joins. Regenerate
by re-running the judge agents; do not expect it to be there.

---

## A. Fix the ceiling bugs, then re-measure the same 5 questions

The point is a before/after on identical questions. Do not change the question set.

### A1. community-opinion cannot pass its own material (PILOT F2)
`reddit.com`, `news.ycombinator.com`, `x.com`, `stackoverflow.com` sit at tier 5 (base 32).
In this mode peer-review and citations are off, HN engagement caps at +18, `trusted_people`
is +12 — so a discussion thread tops out at 50 against a SUPPORT threshold of 62. Observed:
0/60 passed.

Decide which lever to move — they are not equivalent and the choice needs a stated rationale:
- raise these domains' tier *within the mode overlay only* (they are genuinely tier 5 for an
  academic question), or
- lower the mode's verdict thresholds, or
- raise the engagement cap so engagement can actually carry a thread.

Whichever is chosen, add a regression test asserting that a high-engagement HN thread reaches
SUPPORT in this mode. That is a test whose outcome is not knowable by inspection, unlike the
existing golden cases.

### A2. official-docs reduces to the tier table, which lacks the docs sites (PILOT F3)
Mode switches off recency, peer-review and engagement, so `score == tier base`. `nextjs.org`
and `supabase.com` are unregistered → 32 → the canonical answer ranks alongside SEO tutorials.
0/60 passed. Needs a documentation-domain tier set: language/framework/cloud official docs.

### A3. The unregistered default is an assertion nobody made (PILOT F1)
62 of 78 URLs in the news run were unregistered, all scored exactly 32.0. Tier 5 is being used
both as "general media / community site" and as "we have never heard of this", which are
different claims. Options: a distinct `unknown` band, or a signal-driven path that lets an
unregistered domain earn its way up. 424 registered domains does not scale to open search —
adding more is not the fix.

Concrete gaps found in the pilot, useful as test cases: `clevelandclinic.org`,
`theconversation.com`, `nextjs.org`, `supabase.com`, `encore.dev`, `milvus.io`,
`opensourceconnections.com`.

### A4. official-docs treats docs as evergreen; framework docs are not (PILOT F3)
The judge found an archived `nextjs.org/docs/13/pages/...` page and a superseded
`supabase.com/docs/.../auth-helpers` page, both reading as authoritative. Version staleness is
invisible to the policy by construction. Consider a URL-path signal (`/docs/13/`, `/v1/`,
`/legacy/`) — cheap, deterministic, and in the spirit of the existing SEO-path penalties.

### A5. Re-run and compare
Re-run the 5 pilot questions after the above and produce a before/after table on the same
measures. This is the artifact worth putting in the README.

## B. Raise the fetch rate and re-confirm the judge-dependent findings

Pilot exit criteria required `basis: fetched` on a majority of judgments. It failed badly:
7–11 fetched out of 60–78 per run (HN 429, several sites 403). Findings F1–F3 rest on tier
arithmetic and are unaffected. **F4 (precision is fine, recall is the problem) and F6 (vendor
suppression) rest on judge labels and are provisional until this is redone.**

- Add retry/backoff and pacing to the judge agent prompt; accept a smaller URL set (30 instead
  of 60) in exchange for a high fetch rate. Coverage matters less than judgment quality here.
- Consider a second judge on a subset to get an inter-rater agreement figure. Currently there
  is one rater and no reliability estimate — the biggest methodological hole.

## C. Keyword expansion is part of search and is currently unmodelled

Real agent search is iterative: the first result set teaches you the vocabulary, and the
second query is better than the first. The skill has this at the pipeline level (SKILL.md
Step 4, re-search) but it is main-agent judgment, unspecified and unmeasured.

Two separable pieces of work:

1. **In the skill** — specify how the search subagent expands keywords: what triggers an
   expansion, where the new terms come from (titles of what scored well? terms in the
   question's own domain?), and the round cap. Right now Step 4 says "change the keywords"
   and leaves it there.
2. **In this evaluation** — the pilot judged a single-shot result set, which understates what
   real usage produces. The `na2` judge noted its "N+1 problem" query direction returned
   mostly tutorials, i.e. a bad expansion wastes a round; the `ac2` judge noted that
   consumer-phrased queries were what surfaced the vendor pages at all. Expansion changes the
   candidate pool the policy sees, so measuring the policy on a one-shot pool is a different
   experiment from measuring it in use. Decide which one is being claimed.

Note the interaction with A3: expansion surfaces *more* unregistered domains, so a policy that
scores "unknown" as 32 gets worse, not better, as search improves.

## D. Write the pilot report (n=5)

`PILOT.md` is the working record — dense, internal. The portfolio artifact is a different
document, for a reader who has not seen this repo.

Structure that the evidence supports:
- The concrete case first: search rank put vendor re-reports in the top 8 for the EU AI Act
  question (one syndicated law-firm piece at ranks 3, 4 and 7); the policy passed 0 of 42
  vendor sources. Pair it with the original harness-tool anecdote as the same mechanism.
- Then the cost, honestly: recall loss 0.67–1.00 in four of five modes.
- Then the diagnosis: academic mode achieves 0.08 recall loss under the identical policy, so
  this is domain coverage, not scoring design.
- Then the limits, unprompted: n=5, one rater, low fetch rate, no accuracy claim.

Mark it explicitly as pre-fix. If A lands, the after-numbers replace the middle section and
the report becomes a fix narrative instead of a findings list.

## Open questions

- **Is the re-report problem in scope?** `nw1` found the top of search dominated by syndicated
  secondary coverage while primary EU sources sat at rank 16+. SKILL.md Step 5 handles this at
  the *claim* level during verification, but the *scorer* has no notion of primary vs.
  re-report. Adding one is a real feature; deciding it is out of scope is also fine, but it
  should be a decision.
