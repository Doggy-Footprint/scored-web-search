# Pilot results (n=5, one question per mode)

Procedure: METHODOLOGY.md. Judge agents (Sonnet) had no access to `scripts/`, ran plain
web search, and labeled every URL useful/marginal/junk before any score existed.
Raw data in `runs/*.judge.json`, `runs/*.contrast.json`.

| run | mode | n | useful | recall loss | precision loss | vendors passed |
|---|---|---|---|---|---|---|
| ac2 | academic | 60 | 28 | 2/28 = **0.08** | 0/12 = 0.00 | 0 / 12 |
| na2 | non-academic | 60 | 11 | 6/9 = **0.67** | 0/21 = 0.00 | 2 / 14 |
| co2 | community-opinion | 60 | 13 | 10/10 = **1.00** | 0/21 = 0.00 | 0 / 14 |
| nw1 | news | 78 | 32 | 22/32 = **0.69** | 1/12 = 0.08 | 0 / 42 |
| od3 | official-docs | 60 | 13 | 9/9 = **1.00** | 0/13 = 0.00 | 0 / 31 |

## F1. The policy is a domain lookup that mostly misses

Unregistered domains fall to the tier-5 default (base 32) and there is no path back up.

| run | URLs | tier 5 | of which unregistered |
|---|---|---|---|
| ac2 | 60 | 30 | 30 |
| na2 | 60 | 50 | 31 |
| co2 | 60 | 54 | 22 |
| od3 | 60 | 53 | 41 |
| nw1 | 78 | 65 | 62 |

Nearly every recall-loss case scores exactly `32.0` — not a judgment the policy made, but the
absence of one. 424 registered domains is not enough coverage for open web search, and the
default is applied as if it were an assessment.

## F2. community-opinion has a ceiling below its own pass mark

`reddit.com`, `news.ycombinator.com`, `x.com`, `stackoverflow.com` are all tier 5 (base 32).
In this mode the HN engagement cap is +18 and `trusted_people` is +12, and peer-review and
citation paths are off. Maximum reachable score for a discussion thread: **50**. SUPPORT
begins at 62.

A Hacker News thread cannot pass this filter at any engagement level. Observed: 0 of 60 URLs
reached SUPPORT; the highest-scoring item (53.3) was an arXiv paper, i.e. the mode's fallback
to academic scoring outranked every discussion the mode exists to find. The mode is
unusable as written.

## F3. official-docs reduces to the tier table, and the docs sites are not in it

The mode switches off recency, peer-review and engagement, so `score == tier base`. With
`nextjs.org` and `supabase.com` unregistered, the canonical answer to the question scored 32
(WEAK) alongside the SEO tutorials. 0 of 60 passed; 53 were WEAK.

Separately, the judge found real version staleness the policy cannot see by construction:
an archived `nextjs.org/docs/13/pages/...` page and a superseded `supabase.com/docs/...`
auth-helpers page both read as authoritative. Recency-off treats docs as evergreen; framework
docs are not.

## F4. Precision is fine; recall is the whole problem

Across 318 judged URLs the filter promoted **one** junk source (an official EDPS PDF, tier 2,
correct-when-written but predating the 2026 Omnibus delay — a staleness failure, not a tier
failure). It is not letting bad sources through. It is dropping good ones: recall loss 0.67
to 1.00. The skill's stated tradeoff (precision over recall) holds, but the price is far
higher than the README implies.

## F5. Academic mode works, and that tells us why the others do not

`ac2` is the outlier: recall loss **0.08** (26 of 28 useful sources passed), 22 PRIMARY, 0 junk
promoted, all 12 telehealth/supplement vendors suppressed. The same policy, the same procedure.

The difference is coverage where it counts. Medical and scientific publishers -- journals,
PMC/PubMed, Lancet, Cleveland Clinic -- are densely registered in the tier table, so the
30 unregistered domains in this run were mostly the consumer-facing pages that *should*
fall through. In the other four runs the unregistered set contained the answer.

So the policy is not badly designed; it is a well-built instrument for the academic web that
has been extended to four other webs whose domains it does not know. That is a coverage
problem with a clear shape, not a scoring problem.

The two misses are worth naming: a Cleveland Clinic newsroom release reporting the clinic's
own cohort study, and a TheConversation explainer written by the researchers themselves.
Both scored 32 (WEAK) because neither `clevelandclinic.org` nor `theconversation.com` is
registered at all -- the same F1 coverage gap, reaching even into the mode that works.

## F6. Vendor suppression reproduces

This is the one hypothesis the pilot confirms. Vendor-interest sources were 14/60 (na2),
42/78 (nw1), 31/60 (od3), 12/60 (ac2), and search rank favored them heavily — in nw1 the top 8 hits were
almost entirely law-firm and compliance-vendor re-reports, with one syndicated piece
occupying ranks 3, 4 and 7. Under the policy, 0-2 vendor sources passed in every run.

The two that did pass in na2 are the honest cases: the Milvus GitHub repo (SUPPORT 70) and a
SIGMOD paper (PRIMARY 100) — vendor-affiliated but substantive, and the judge called both
useful. So the mechanism behind the harness-tool anecdote is real and reproducible: the tier
table demotes commercially-motivated content that search rank promotes.

## Methodology limitation, stated plainly

Pilot exit criteria required `basis: fetched` on a majority of judgments. **This failed:**
9-11 of 60-78 per run were fetched; the rest were judged from title and snippet, with HN
returning 429 and several sites 403. Snippet-basis judgments are weaker on exactly the axis
that matters (a page that looks substantive but is filler). F1-F3 rest on tier arithmetic
that snippet-basis cannot affect and stand regardless. F4 and F5 depend on judge labels and
should be read as provisional until re-run with a higher fetch rate.

## Implications

1. Fix the ceiling before anything else — F2 and F3 are arithmetic bugs, not tuning issues.
2. Registering more domains does not scale. The unregistered default needs to be either
   evidence-driven or explicitly labeled "unknown" rather than scored as mediocre.
3. Report the vendor-suppression result (F5) as the skill's demonstrated effect, and F4 as
   its measured cost. Both are more defensible than the token-reduction number.
