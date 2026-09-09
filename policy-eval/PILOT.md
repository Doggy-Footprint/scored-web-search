# Policy validation, n=5

Procedure: `METHODOLOGY.md`. One question per mode, each chosen as that mode's hardest case.
Judge agents (Sonnet) had no access to `scripts/`, ran plain web search, and labelled every URL
useful/marginal/junk before any score existed. Raw data in `runs/*.judge.json`;
`runs/before/*.contrast.json` holds the pre-fix scoring, `runs/*.contrast.json` the current one.

The policy was measured, found broken in two modes, fixed, and re-measured against the same
judge labels. Both sets of numbers are below, because the pre-fix numbers are what make the
diagnosis legible.

| run | mode | n | useful | recall loss | precision loss | vendors passed |
|---|---|---|---|---|---|---|
| ac2 | academic | 60 | 28 | 0.08 → **0.00** | 0.00 → 0.00 | 0/12 → 0/12 |
| na2 | non-academic | 60 | 11 | 0.67 → **0.25** | 0.00 → 0.00 | 2/14 → 2/14 |
| co2 | community-opinion | 60 | 13 | 1.00 → **0.50** | 0.00 → 0.00 | 0/14 → 0/14 |
| nw1 | news | 78 | 32 | 0.69 → 0.69 | 0.08 → 0.08 | 0/42 → 0/42 |
| od3 | official-docs | 60 | 13 | 1.00 → **0.23** | 0.00 → 0.09 | 0/31 → **7/31** |

Re-measurement did not require re-judging. Judge labels are produced without access to the
policy — that separation is the whole design of `METHODOLOGY.md` — so they are a fixed
reference, and `contrast.py` re-scoring the stored judgments is a valid before/after.

---

## F1. The policy is a domain lookup, and its coverage is the whole story

Unregistered domains fall to the tier-5 default (base 32) and there is no path back up.

| run | URLs | tier 5 | of which unregistered |
|---|---|---|---|
| ac2 | 60 | 30 | 30 |
| na2 | 60 | 50 | 31 |
| co2 | 60 | 54 | 22 |
| od3 | 60 | 53 | 41 |
| nw1 | 78 | 65 | 62 |

Nearly every pre-fix recall-loss case scored exactly `32.0` — not a judgment the policy made,
but the absence of one.

**Decision: the default stays.** The project's premise is that a trustworthy source leaves a
trace somewhere, and in this policy the trace is registration. A signal-driven path up for
unknown domains would let the scorer assert credibility it never checked. So the answer to a
coverage gap is to register the domain. ~190 were added in the fix round (documentation,
community forums, medical institutions), and `na2` improved 0.67 → 0.25 from that alone,
with no mode overlay touched. The cost is stated rather than engineered away — see F5.

## F2. community-opinion had a ceiling below its own pass mark

`reddit.com`, `news.ycombinator.com`, `x.com`, `stackoverflow.com` sat at tier 5 (base 32).
With peer-review and citations off, HN engagement capped at +18 and `trusted_people` at +12,
a discussion thread topped out at **50** against a SUPPORT threshold of 62. A Hacker News
thread could not pass at any engagement level. Observed: 0 of 60 reached SUPPORT, and the
highest-scoring item (53.3) was an arXiv paper — the mode's fallback to academic scoring
outranked every discussion the mode exists to find.

**Fixed** by giving the mode its own reading of the tier table rather than a bonus on top of
the academic one: discussion hosts are tier 1 in this mode, because the question here is what
practitioners actually hit and for that question the thread *is* the primary source. Promotion
alone would overcorrect, so tier and engagement were given different jobs — the tier says the
venue is where the answer lives, `engagement_floor` says whether anyone was in the room.

## F3. official-docs reduced to the tier table, and the tier table capped it below its own pass mark

The mode switches off recency, peer-review and engagement, so `score == tier base`. 0 of 60
passed; 53 were WEAK.

The first diagnosis of this was wrong and is worth recording, because acting on it as written
would have fixed nothing. It read "the docs sites are not in the table" — but `nextjs.org` was
already registered at tier 3 as of `6282aa4`. Tier 3 is base **60** and SUPPORT begins at 62,
so with every signal off a *registered* documentation domain could not pass either. The
ceiling, not the coverage, was the binding constraint; adding domains at tier 3 would have
moved nothing.

**Fixed** by re-filing documentation and specifications as tier 1 in this mode only. With every
signal off, the tier table here has to mean "how canonical is this for this API", not "how
academic is this". Vendor engineering blogs are not promoted — they are commentary, not the
reference.

## F4. Precision is fine; recall was the whole problem

Across 318 judged URLs the pre-fix filter promoted **one** junk source (an official EDPS PDF,
tier 2, correct-when-written but predating the 2026 Omnibus delay — a staleness failure, not a
tier failure). It was not letting bad sources through. It was dropping good ones. After the
fix, precision loss is still 0 in three of five modes; the two cases in `od3` are both version
staleness (F9), which is the same failure mode as the original one.

## F5. Academic mode always worked, and that is why the others did not

`ac2` was the outlier before the fix: recall loss 0.08, 22 PRIMARY, 0 junk promoted, all 12
telehealth/supplement vendors suppressed. Same policy, same procedure. The difference is
coverage where it counts — medical and scientific publishers are densely registered, so the 30
unregistered domains in that run were mostly consumer-facing pages that *should* fall through.
In the other four runs the unregistered set contained the answer.

The policy was never badly designed. It was a well-built instrument for the academic web,
extended to four other webs whose domains it did not know, in two cases through a mode overlay
that removed the only signals that could have compensated.

Its two remaining misses were a Cleveland Clinic newsroom release reporting the clinic's own
cohort study and a TheConversation explainer written by the researchers themselves. Both are
now registered, and `ac2`'s recall loss is 0.00.

## F6. Vendor suppression reproduces, and is a property of the mode

Vendor-interest sources were 14/60 (na2), 42/78 (nw1), 31/60 (od3), 12/60 (ac2), and search
rank favoured them heavily — in `nw1` the top 8 hits were almost entirely law-firm and
compliance-vendor re-reports, with one syndicated piece occupying ranks 3, 4 and 7. Under the
policy, 0–2 vendor sources pass in four of the five modes. The two that pass in `na2` are the
honest cases: the Milvus GitHub repo (SUPPORT 70) and a SIGMOD paper (PRIMARY 100) —
vendor-affiliated but substantive, and the judge called both useful.

`od3` now deliberately reverses this: 7 of 31 vendor sources pass, against 0 before. That is
not a regression. The mode's question is "how does this API behave", and for that question the
vendor's own reference **is** the primary source — Supabase's auth docs are the answer to a
Supabase auth question. The mechanism behind the original harness-tool anecdote is real and
reproducible, but it is a mode-level property, not a property of the skill.

## F7. The HN engagement signal could not see HN itself

Found only by re-measuring, and it made the F2 fix look like it had failed. `hn_points()`
queries Algolia with `restrictSearchableAttributes=url`, but an HN story record carries the
*article's* URL, never its own permalink — so `news.ycombinator.com/item?id=N` matched nothing,
scored 0 points, and then took the new engagement-floor penalty. All five HN threads the judge
called useful in `co2` landed in SKIM at 56.0: the tier fix and the fetcher gap cancelled out
to look like no change at all.

`extract_hn_item_id()` + `hn_item()` now look the thread up by object id. Those five score
99.9–100.0 (HN 94–460), which is what moved `co2` from 1.00 to 0.50.

## F8. An unasked lookup is not silence

The engagement floor as first written treated "no lookup" the same as "no engagement", so a
`--no-net` run and a rate-limited lookup both read as a dead thread. The pilot itself ran with
HN returning 429 on most lookups, which would have turned a rate limit into a scoring penalty
— and would have repeated, inside the new term, exactly the F1 mistake of applying the absence
of a judgment as if it were one. The floor now fires only when a lookup actually returned:
a successful lookup that finds no discussion is real silence, an unreachable one is no
evidence either way.

## F9. Version staleness is only partly reachable from a URL

`version_path` catches the explicit case: `nextjs.org/docs/13/...` scores 60 instead of 88,
out of the citable range, and it left the precision-loss set.

Which release is current is a fact about the world on the day you ask, so the policy does not
assert it. `--doc-version nextjs.org=14` pins what the reader is on and anything else on that
domain is the wrong manual; with nothing pinned, the fallback reads only the shape that signals
"superseded" on its own — a bare major line (`/docs/13/`, `/docs/v2/`) kept alongside a newer
one. A precise release (`/doc/2.1/`, `/docs/1.7.0/`) is left alone, because that is how numpy
and others publish the *current* release; independent verification caught
`numpy.org/doc/2.1/` being penalised as an archive when it is the live manual. A URL with no
version segment (`/docs/`, `/doc/stable/`, `/latest/`) is the site's own pointer at what is
current and is never penalised.

What it cannot reach: the one remaining precision loss in `od3` is
`supabase.com/docs/guides/auth/auth-helpers/nextjs` — a deprecated *package*, at a live URL on
a current docs site, with nothing in the path to read. A second case, `next-auth.js.org`, was
handled by de-registering the domain, since the whole site is superseded by `authjs.dev`; that
is a registration decision, not a scoring one. Detecting deprecation inside page content is out
of reach for a policy that never fetches the page.

---

## What the fix changed

1. **`domain_overrides`** (`srcscore_core/policy.py`). A mode re-files specific domains into
   different tiers instead of receiving a bonus on top of the academic tier table. The merge
   removes each domain from its old tier and appends it to the new one, so the result is an
   ordinary `domains` dict and `match_tier` needs no knowledge of any of it.
2. **community-opinion**: discussion hosts → tier 1, plus `engagement_floor` (−36 below 10 HN
   points / GitHub stars, and only when a lookup returned).
3. **official-docs**: official documentation and specifications → tier 1.
4. **`penalties.version_path`** (−28) with `--doc-version`, sharing `path_penalty()` with the
   SEO-slug check. Both read the URL *path* only — a `?next=/docs/13/x` redirect parameter is
   not a claim about the page being served, and such parameters are common on exactly the
   auth/docs URLs this fires on.
5. **Domain coverage**: ~190 documentation, community-forum and medical-institution domains.
   The unregistered default stays at tier 5 / 32.
6. **`hn_item()`** (F7).

## What did not change, and why

- **news (0.69) is untouched.** The fix addressed two mode ceilings and expanded
  technical/medical coverage; `nw1`'s 22 losses are unregistered news, legal and
  policy-analysis domains — the F1 coverage problem in the one field the round did not cover.
- **`co2` still loses 5 of 10.** All five are practitioner blog posts on unregistered personal
  or platform domains (`michael.bouvy.net`, `levelup.gitconnected.com`, ...). Under the F1
  decision this is the accepted, stated cost: the mode can now pass the discussion threads it
  exists to find, and personal blogs remain a registration question.
- **The engagement floor is a step, and this mode has almost no SUPPORT band for threads.**
  A thread is either below the floor (SKIM) or carries the full tier-1 base (PRIMARY). The
  step is the intended claim — below the floor the thread is not evidence — but the missing
  middle band is a real consequence, not a designed one.

## Limits

- **Fetch rate.** The exit criteria required `basis: fetched` on a majority of judgments. This
  failed: 7–11 of 60–78 per run, with HN returning 429 and several sites 403. The rest were
  judged from title and snippet, which is weakest on exactly the axis that matters — a page
  that looks substantive but is filler. F1–F3 and F7–F9 rest on tier arithmetic and code paths
  that snippet-basis cannot affect. **F4, F5 and F6 rest on judge labels and are provisional
  until this is redone.** The before/after numbers inherit the limitation exactly.
  This is a limitation of the judge step, not of the skill: the judge runs plain web search
  with no access to the policy, so it — not `scored-web-search` — decides whether to open a
  page. Of the 271 snippet-basis judgments across all five runs, only 42 were an actual fetch
  failure (403/429); the other 229 were the judge treating the case as obvious from the
  snippet alone. Resolved as a scored-web-search question; the weak-basis limitation on
  F4/F5/F6 itself stands regardless of cause. See TODO.md B.
- **One rater.** No second judge, so there is no inter-rater agreement figure. This is the
  largest methodological hole.
- **n=5.** Case studies with a consistent procedure, not a benchmark.
- **No accuracy claim.** Nothing here shows that reports written from filtered sources are more
  *correct*. That needs claim-level verification against ground truth and is out of scope.
