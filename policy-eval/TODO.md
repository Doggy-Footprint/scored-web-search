# Policy validation — next steps

State: the n=5 pilot has been run, the two mode ceilings it found are fixed, and the same five
questions have been re-measured. Read `METHODOLOGY.md` first, then `PILOT.md`.
`questions.json` holds all 30 questions (5 modes x 6, the 6th of each being a two-round keyword-expansion question); 5 have been run.

Tracked: `METHODOLOGY.md`, `PILOT.md`, `questions.json`, `contrast.py`, this file.
Untracked (`.gitignore`): `runs/` — raw judge output, URL lists, contrast joins, and
`runs/before/` (the pre-fix scoring, kept as the before-side of the comparison).

---

## A. news-mode domain coverage

The one mode the fix round did not touch, and now the largest single number in the table:
recall loss **0.69**, unchanged. All 22 losses are unregistered news, legal and
policy-analysis domains — the F1 coverage problem in a field the round did not cover.

This is the same work that took `na2` from 0.67 to 0.25: register the domains. Wire services,
national papers of record, legal trade press, think-tank and regulator commentary, and the
Korean equivalents. The pilot's `nw1` judge output is the source of concrete gaps.

Decide alongside it: **is primary-vs-re-report in scope for the scorer?** `nw1` found the top
of search dominated by syndicated secondary coverage while primary EU sources sat at rank 16+.
`SKILL.md` Step 5 handles this at the *claim* level during verification, but the scorer has no
notion of primary vs. re-report. Adding one is a real feature; deciding it is out of scope is
also fine, but it should be a decision, and it is most naturally made while looking at this run.

## B. Raise the fetch rate and re-confirm the judge-dependent findings

Exit criteria required `basis: fetched` on a majority of judgments and it failed badly: 7–11
of 60–78 per run (HN 429, several sites 403). **F4, F5 and F6 rest on judge labels and are
provisional until this is redone**; the post-fix numbers inherit the same limitation.

**Resolved: this is not a scored-web-search defect.** The judge runs plain web search with no
access to `srcscore.py`/`policy.json` (see METHODOLOGY.md) — the choice not to open a page is
made entirely inside the judge step, before the policy ever sees a URL. Auditing
`runs/*.judge.json` by `basis` across all 318 judgments: 271 were snippet-basis, and of those
only 42 were an actual fetch failure (9 × 403, 33 × 429, nearly all HN rate-limiting in `co2`).
The other 229 were the judge choosing snippet-only because it considered the case obvious
(mostly `medium.com`, `dev.to`, `arxiv.org`). So the low fetch rate is a property of the judge
agent's own behaviour during plain search, not of the skill or the policy — but the numbers
below are still what's provisional, since the *reason* being external doesn't make the
snippet-basis judgments any less weak on the "looks substantive but is filler" axis:

- Add retry/backoff and pacing to the judge agent prompt; accept a smaller URL set (30 instead
  of 60) in exchange for a high fetch rate. Coverage matters less than judgment quality here.
  Note this only helps the 42 external-failure cases — the 229 "judge decided it was obvious"
  cases need an instruction change (e.g. require fetch unless confidence is very high), not
  retry/backoff.
- Add a second judge on a subset to get an inter-rater agreement figure. There is currently one
  rater and no reliability estimate — the biggest methodological hole in the whole procedure.

## C. Keyword expansion is part of search and is currently unmodelled

Real agent search is iterative: the first result set teaches you the vocabulary, and the second
query is better than the first. The skill has this at the pipeline level (`SKILL.md` Step 4,
re-search) but it is main-agent judgment, unspecified and unmeasured.

1. **In the skill** — specify how the search subagent expands keywords: what triggers an
   expansion, where the new terms come from (titles of what scored well? terms in the
   question's own domain?), and the round cap. Step 4 currently says "change the keywords".
2. **In this evaluation** — the pilot judged a single-shot result set, which understates what
   real usage produces. The `na2` judge noted its "N+1 problem" query direction returned mostly
   tutorials, i.e. a bad expansion wastes a round; the `ac2` judge noted that consumer-phrased
   queries were what surfaced the vendor pages at all. Expansion changes the candidate pool the
   policy sees, so measuring the policy on a one-shot pool is a different experiment from
   measuring it in use. Decide which one is being claimed.

**Specified.** Both questions are now answered on paper.

*In the skill* (`SKILL.md`): expansion has one path, and it runs on read text. A term found in
the **body of a source that passed** goes to a **new subagent** under the same shape as Step 1
(`SKILL.md` Step 3) — never to a search the main agent runs itself, so the reason to delegate
Step 1 at all still holds on round two, and the identical prompt skeleton keeps the KV cache.
The round cap stays at two, with the user asked before a third — unchanged from before this
round, and now stated in Step 4 where the re-search rule lives rather than mid-Step 3.

The rejected alternative is worth recording: letting the *search* subagent expand from result
titles. A title is not evidence that a term is the right one — it is exactly the snippet-grade
material Step 1 exists to keep out — so an expansion decided from titles is a guess made on the
weakest input in the pipeline, and it fires before anything has been scored.

*In the evaluation*: both experiments are claimed, separately. The 25 original questions stay
one-shot; five new `expansion: true` questions (`ac6`, `na6`, `co6`, `nw6`, `od6`) are judged in
two rounds — round 1, keywords taken from the body of round-1 sources the judge actually opened,
round 2 — with `round` on every judgment and `expansion_keywords` at the top of the judge file
(`METHODOLOGY.md`). `contrast.py` splits the report by round and counts round-2 tier-5 and
unregistered landings, which is the F1 interaction below measured rather than asserted. Nothing
is pooled across the two sets.

**Still to do: run them.** No `expansion: true` question has been judged yet, so there is no
number here — only a specification and the machinery to score it. These runs also depend on B:
a keyword can only be derived from a source the judge opened, so a run with the pilot's fetch
rate would produce almost no round two at all.

Note the interaction with F1: expansion surfaces *more* unregistered domains, so the coverage
work in A gets more important as search improves, not less.

## D. Write the report

`PILOT.md` is the working record — dense, internal. The portfolio artifact is a different
document, for a reader who has not seen this repo. The evidence now supports a fix narrative
rather than a findings list:

- The concrete case first: search rank put vendor re-reports in the top 8 for the EU AI Act
  question (one syndicated law-firm piece at ranks 3, 4 and 7); the policy passed 0 of 42
  vendor sources. Pair it with the original harness-tool anecdote as the same mechanism.
- Then the cost, honestly: recall loss 0.67–1.00 in four of five modes, pre-fix.
- Then the diagnosis: academic mode achieved 0.08 under the identical policy, so this was
  domain coverage and two mode ceilings, not scoring design.
- Then the fix and the after-numbers, including the two findings that only re-measurement could
  produce (F7, F8) and the one deliberate reversal (F8 vendor behaviour in official-docs).
- Then the limits, unprompted: n=5, one rater, low fetch rate, no accuracy claim.

## Open

- **The engagement floor has no middle.** In community-opinion a thread is either below the
  floor (SKIM) or carries the full tier-1 base (PRIMARY); there is almost no SUPPORT band for
  threads. The step itself is the intended claim, but the missing band is a side effect. Worth
  revisiting if a run produces threads that ought to land between the two.
- **Deprecation is invisible without fetching.** `--doc-version` and the bare-major fallback
  cover versioned URLs; a deprecated *package* documented at a live URL on a current site
  (`supabase.com/docs/.../auth-helpers/`) is unreachable from the URL alone. Either accept it
  as a stated limit or decide that this mode is allowed to fetch.
