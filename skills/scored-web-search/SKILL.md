---
name: scored-web-search
description: Filter and rank web sources with heuristic scoring system before the main agent reads them. By using only select sources, this skill reduces context rot, improves the quality of research reports, and keeps low-quality page text out of the main context. This skill provides academic, non-academic, community-opinion, news, and official-document modes. Triggering expressions are "scored web search", "search", "research", "investigate", "find", "look up", "back it up", "evidence", "fact check", and similar. This skill intentionally drops some of web-search results - if you need those for example, searching for more possible options, please use regular web search instead.
---

# Scored Web Search

This skill filters sub-standard sources before the main agent reads them when high quality research is on demand. 

## Pipeline

```
1.   Search   (lightweight subagent)        → URL/title/date only, no summaries
2.   Score    (`scripts/srcscore.py`)        → 0-100 + verdict
2.5. Judge    (lightweight subagent)        → SUPPORT only: USE / SKIP / UNJUDGED
3.   Read     (main agent)                        → PRIMARY + USE-judged SUPPORT only
4.   Re-search (loop back to 1 if sources are thin)
5.   Verify / follow-up (required in academic; optional otherwise) → claims vs. evidence, trace to originals
6.   Write    → every claim tagged with source + score
```

Paths below are relative to this skill's folder. Steps 1, 2.5 and 5 are delegated to lightweight subagents; each step says what to do without them.

### Step 1 — Search: collect URLs only

The main agent defines the search keywords. Delegate the actual web search to a **lightweight subagent** — do NOT use `fork`; use luna, haiku, or the lightest model of the same generation. Give it `references/searcher-prompt.md` as its instructions, its queries, and a file path you name under `$TMPDIR` (`/tmp` only when it is unset; `echo $TMPDIR` tells you), one file per subagent, e.g. `$TMPDIR/ssw/urls-1.json`. Sandboxes let files be written and read there but often not in `/tmp`. It writes JSON records of `url`, `title`, and an optional ISO `date` taken from the search results to that file, and returns only the path and the record count, so neither the records nor any search-result text reaches the main context. NO summaries, NO snippets, DO NOT open page — the point of this step is to keep low-quality text out of the main context.

If the subagent could not write the file, it returns the records themselves. Write them to the path you named, go on, and count it under `step1-inline` in the report's `Degraded:` line (Step 6). They cost main context but carry no page text.

Max 2 sub-topics per subagent. For 3 or more sub-topics, run subagents in parallel. Collect 60-90 URLs total at most.

In `official-docs` mode, name the official docs host of the product the question is about (one or two, e.g. `docs.python.org`) and add `site:<host> <keywords>` queries next to the plain ones. Plain searches often miss the official page entirely.

No sub agent fallback: main agent calls the web-search tool itself. Extract only URL/title and any verified publication date from the results and write these JSON records straight to `urls.json` under `$TMPDIR`, as above. The caution for sub agents works same for main agent too.

### Step 2 — Score: hand it to the script

Pass every subagent's file to `scripts/srcscore.py` (`--in` is repeatable; `--help` for arguments) with `--format compact`.

When the question names a docs version or variant, pass it: `--doc-version nextjs.org=14`, `--doc-variant nextjs.org=app` (variants are listed under `doc_variants` in `scripts/policy.json`). Pages for another version or variant then score as `stale-version` / `wrong-variant`. Without them, only paths that read as superseded on their own are penalised.

In `official-docs` mode, pass the hosts you used for `site:` queries: `--official-domain biomejs.dev`. A host the policy does not know is then scored SUPPORT with `official-claimed` instead of the unregistered default, so Step 2.5 still vets each page. A registered host keeps its tier.

Output lists only the PRIMARY and SUPPORT rows, each with its title cut to 40 characters, and a count per verdict:

```
SCORE VERDICT T  SIGNALS                     URL  TITLE
 98.0 PRIMARY  3  cit=132.0k,y2017,published@ https://arxiv.org/abs/1706.03762  Attention Is All You Need
 77.9 SUPPORT  3  cit=120,y2026,preprint      https://arxiv.org/html/2601.xxxxx  A survey of ...
-- 47 sources | PRIMARY 1 | SUPPORT 1 | SKIM 9 | WEAK 12 | DROP 22 | BLOCKED 2
```

Copies of one paper (arXiv abs/pdf/html pages and versions, one DOI or PMID) and repeated URLs share one row, the best-scored copy; the footer ends with `merged k` when k rows were folded in.

These rows are everything you choose from. The URL files and any `--full-out` table stay out of the main context: do not open, print, or grep them, not even for titles or for what scored below SUPPORT. If the rows are too thin, that is Step 4.

The URL column is the address to fetch: links WebFetch cannot follow are rewritten (doi.org links to the page they resolve to, old PMC links to `pmc.ncbi.nlm.nih.gov`, recent arXiv abs/pdf pages to their HTML full text).

| Verdict | Score | What to do with it |
|---|---|---|
| PRIMARY | 78+ | Cite directly. Valid source for figures and claims |
| SUPPORT | 62-77 | Supporting evidence. Never the sole basis for a conclusion |
| SKIM | 46-61 | Counted, not listed. Do not open |
| WEAK | 30-45 | Counted, not listed. **Do not cite** |
| DROP | <30 | Counted, not listed. Do not open |
| BLOCKED | 0 | Retracted paper / scraper. Counted, not listed |

### Step 2.5 — Judge: vet SUPPORT sources

Whenever there are SUPPORT rows, this step is required: hand every SUPPORT URL, with the research question, to a **lightweight subagent** (same model rule as Step 1, no `fork`) and give it `references/judge-rubric.md` verbatim as its only instructions. It reads each page with `WebFetch` and returns only one line per URL: `url | USE/SKIP/UNJUDGED | reason`. Page text never reaches you. With no SUPPORT rows there is nothing to judge, which is not a shortfall.

No sub agent fallback: only when no subagent can be launched at all, skip this step, read SUPPORT sources as before, and put `judge-unavailable` in the report's `Degraded:` line. A run that skipped it otherwise puts `judge-skipped` there, never `judge-unavailable`.

- Only SUPPORT is judged. PRIMARY is never judged, and nothing at SKIM or below is ever promoted.
- Check the reply against the URLs you sent. Send any URL it left out to a judge once more, with the same rubric. A URL still without a verdict is UNJUDGED with the reason `missing:`.
- A verdict on a source is final for this run: SKIP and UNJUDGED sources stay out of Step 3, and you do not open them to second-guess the judge. An UNJUDGED reason starting `page:` means the site gave nothing to judge; `fetch-error:` means WebFetch failed on it twice. Some sources failing to fetch is still a set of verdicts.
- A judge that could not work at all gave no verdicts. That is when it errors, or when every source is UNJUDGED with `fetch-error`: the judge could not reach the network, which says nothing about the pages. Then read SUPPORT sources as before and put `judge-unavailable` in the report's `Degraded:` line.

### Step 3 — Read: open only what passed
`WebFetch` only PRIMARY sources and USE-judged SUPPORT sources. **Open nothing else.** The moment you open a source that did not pass to judge it for yourself, the savings are gone — that is the exact problem this skill exists to solve.

When a WebFetch note says the page was read only up to some character, call again with the `offset` it gives only if what the question needs is likely in the unread part. Anything you did not read is "beyond what was read", never "not reported".

Default cap: 8-12 sources. Up to 20 if the user asks for depth.

### Step 4 — Re-search: loop back to 1 when sources are thin

When too few sources pass, do not promote anything below SUPPORT. Change the keywords and re-run from Step 1.

Two re-search rounds max. Beyond that, **ask the user explicitly** before searching again, and if they decline, state plainly in the answer that the evidence base is thin.

### Step 5 — Verify / follow-up: claims vs. evidence

Follow-up: as with any web search, terms found in sources you read may be searched. Never run that search yourself; send it through Step 1 and Steps 2-2.5. It counts toward the two re-search rounds.

Verify: required in `academic` mode whenever Step 3 read anything; a run that did not run it puts `verify-skipped` in the report's `Degraded:` line. In other modes, only when the user has stressed accuracy, or the report carries a lot of figures. Give the URLs you read in Step 3 to a **lightweight subagent** and have it do **this and nothing else**:

> For each figure or claim: (a) does this document actually state that figure, (b) is this document the original source of the figure or is it citing someone else, (c) are sample size, time period, and measurement method stated. Answer only in the format `claim | supported/secondary/contradicted | location of evidence`.

When a figure turns out to be secondary, the document you read is not its source, and tracing it is required. Take the URL of the original it cites — a new source, not yet scored — and run that through steps 2-2.5 (when it names the original without a URL, find it through Step 1); if it passes, read it and attribute the figure to it. If the original is paywalled, dead, or fails step 2, keep the figure attributed to the document you read and mark it as a re-report — never present a re-report as a primary source. Tracing originals is one round of its own (`trace`); it does not count toward the two re-search rounds.

No sub agent fallback: only when no subagent can be launched, run the same claim-vs-evidence check yourself instead of delegating it. This step doesn't reintroduce the context-pollution problem Step 1 guards against — the sources are already open and being read — so there's no quality loss from doing it inline.

### Step 6 — Write

Tag every figure and claim with its source and score: `... rose 32% (Nature 2025, PRIMARY 91)`. SKIM-or-below sources are never used. Any claim resting on SUPPORT sources alone gets a hedge — "not yet confirmed", "according to a single report".

Close the report with three lines, in this order:

```
n sources collected → m passed → j judged-out → l read
Degraded: none
Keywords: <round>: q1, q2 / <round>: q3
```

`j` counts the SUPPORT sources the judge kept out (SKIP and UNJUDGED); it is 0 when the judge was unavailable or skipped, since those sources were read. `Degraded:` is always there, so a reader can tell a clean run from a line left out. It lists, joined by ` · ` and summed over all rounds, every way this run fell short of the pipeline, and says `none` only when nothing did:
- `step1-inline <k>/<n>`: k of n search subagents returned their records instead of writing the file.
- `lookups-failed <f>/<t>`: srcscore's stderr reported f of t external lookups unreachable, so those rows were scored without their signals.
- `judge-unavailable`: the Step 2.5 judge was launched and gave no verdicts, or no subagent could be launched at all.
- `judge-skipped`: SUPPORT rows got no Step 2.5 judge although a subagent could be launched.
- `unjudged <u> (fetch-error <f>, page <p>, missing <m>)`: SUPPORT sources the judge left UNJUDGED, by reason; `missing` counts those it left unanswered twice. Not listed in a round whose judge was unavailable; `judge-unavailable` covers it.
- `verify-skipped`: `academic` mode, and Step 5 Verify did not run.

`Keywords:` lists the keywords given to Step 1, in round order including re-search, follow-up and trace rounds.

## Scoring Policy

The domain tier sets the base score; secondary signals (citations, engagement, recency) adjust it. Recency applies once to every source with a known date: academic API publication date/year, repository `pushed_at` or direct HN thread creation date, then supplied publication date. Invalid or future dates fall through to the next candidate. Unknown dates receive no recency adjustment and carry `date-unknown`; `official-docs` disables recency. URL paths can also cost a source one band (-28): `non-content` for listings and build artefacts (a site's `/search` page, `.diff`, `__pycache__`, copies of installed packages), `stale-version` for superseded docs or a registry page pinned to one release, `wrong-variant` for another docs variant than the one passed.

| Tier | Base | Trust level |
|---|---|---|
| 1 | 88 | The primary source itself |
| 2 | 74 | Original work from a vetted institution |
| 3 | 60 | Original work with no review process, or commentary on a primary source |
| 4 | 46 | Second-hand relay. Cross-checking only |
| 5 | 32 | No basis for trust (default for unregistered domains) |
| 6 | 14 | Low quality / spam |
| block | 0 | Always BLOCKED |

The same domain sits at different tiers in different modes, because a mode asks a different question. Read the actual placement off the score table.

## Modes

`--mode` sets the goal of the search.

| Mode | Emphasis on |
|---|---|
| `academic` (default) | Base policy as-is |
| `non-academic` | GitHub / engineering blogs. Engagement (stars, HN) weighted higher |
| `community-opinion` | Discussion threads. Peer-review off, engagement weighted highest, short half-life, discussion hosts treated as primary sources |
| `news` | Reporting. Peer-review off, recency dominant, own tier ladder |
| `official-docs` | Product/framework docs. Recency, peer-review and engagement all off; official docs treated as primary sources |
## Error handling
If `scripts/srcscore.py` fails, report it and stop; do not fall back to unscored reading. Exit 4 means most citation, star and HN lookups could not reach their APIs, so every score would rest on the domain tier alone. Tell the user the hosts to allow — the script names them — and stop. Do not re-run with `--no-net`: it drops those signals, and its verdicts are not this skill's.

## Don't Do This

- Don't cite SKIM-or-below material "for reference anyway."
- Don't pile search-result snippets into the main context — the subagent hands back a file of URL records, never search-result text.
- Don't trust content just because its domain score is high. The tier is a device for choosing what to read, not a guarantee that the content is true.
- Don't double-check a score by opening what it kept out.
