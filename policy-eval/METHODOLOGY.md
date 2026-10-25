# Evaluation Methodology

Two questions, measured on the same questions:

- **A — policy.** Do `scripts/policy.json` + `scripts/modes/*.json` verdicts agree with an independent judge?
- **B — Step 2.5 judge.** Do `judge_support` USE/SKIP decisions agree with the same judge?
- **Arms — with vs. without the plugin.** How much low-quality material reaches the main agent's context,
  how many sources it reads, and what the whole run costs, with the plugin loaded and with no plugin at all.

Report quality (claim-level accuracy of the written answer) is **not** measured. Reports are written only so the
cost of a full run is real.

## Independence

The blind judge never sees `srcscore.py`, `policy.json`, tiers, verdicts or which arm a URL came from. If the
policy's output reaches the judge, the policy grades its own exam.

## Runs (per question)

1. **Blind judge** (Workflow agent, Opus 5.5, effort medium, no skill/plugin tools, no policy files).
   Plain WebSearch, 40-60 URLs, each labelled `useful` / `marginal` / `junk` against the question, with
   `vendor_interest`, `basis` (`fetched` | `snippet`), `rank`, and a short reason. Writes
   `runs/<id>.judge.json` and `runs/<id>.urls.json` (URL/title/search-result date).
   `expansion: true` questions are judged in two rounds: round-2 keywords come only from the body text of
   round-1 pages the judge fetched, recorded as `expansion_keywords` `{keyword, source_url}`; entries carry `round`.
2. **Arms** (`claude plugin eval`, one case per question, `policy-eval/arms/<id>/case.yaml`, Opus 5.5).
   The same prompt runs twice: with the plugin from this repo, and with no plugin (temp HOME/config, no
   CLAUDE.md in either arm). The prompt names "scored web search" only as words, so the baseline is an
   ordinary research agent. `tool_used: Skill` records whether the skill fired in the with-arm.
3. **Ingest** (`arms.py ingest <result.json>`). Copies each run's stream trace and session transcripts into
   `runs/`, extracts events into `runs/<id>.<arm>.events.json`, and lists the arm URLs the blind judge has not
   labelled in `runs/<id>.union.todo.json`.
4. **Union labeller** (Workflow agent, same model and rules as 1). Labels the todo URLs, shuffled and without
   arm information, by the same criteria → `runs/<id>.union.judge.json`.
5. **Analyze** (`arms.py analyze`, `contrast.py <id>`). Scores every URL with the real scorer and joins.

## Exposure levels

Each URL an arm touched is counted once, at the highest level it reached:

| Level | Meaning |
|---|---|
| `read` | the main agent fetched the page (WebFetch succeeded) |
| `exposed` | the URL came back from a WebSearch the main agent ran — that tool returns a text summary of its results, so their content is in the main context |
| `listed` | only the URL/title is in the main context: searcher hand-back, score table, or a search result with no summary |
| `sub_read` | a subagent or the `judge_support` tool fetched it; nothing of it is in the main context |
| `sub_seen` | only a subagent's search returned it |

**Contamination** = `marginal` + `junk` URLs at a main-context level (`read`, `exposed`, `listed`), reported
per level × label × tier. The tier comes from scoring every URL with the current policy, in both arms, so the
baseline's sources are placed on the same ladder.

## Cost

USD at list price (`pricing.py`). Sources, most authoritative first:

- `result.modelUsage` (per model, every API call of the run, including tools' internal calls and web-search fees).
- Session transcripts (`config/projects/<cwd>/<session>.jsonl` and `subagents/agent-*.jsonl` in the kept
  sandbox): final per-message `usage` → cost per agent role (`main`, `searcher`, other subagents). The stream
  trace's per-message usage is interim and is not used for cost.
- `judge_support` calls go through `$.model.complete` and appear in no transcript; the mod writes
  `.tmp/judge-usage-<ms>.json` (`question`, `model`, `calls[{url, line, usage}]`) and ingest matches each file
  to the run whose `judge_support` output has the same URLs.
- `tool_internal_usd` per model = `modelUsage.costUSD` − transcript cost − web searches × $0.01. It is the
  model calls the WebSearch/WebFetch tools make on their own (WebSearch runs on the caller's model,
  WebFetch's summariser on Haiku), plus `judge_support` if the host bills it into `modelUsage`.

## Measures

- **A**: recall loss (`useful` scored below SUPPORT), precision loss (`junk` scored PRIMARY/SUPPORT), vendor
  pass-through, per-round split for expansion questions (`contrast.py`).
- **B**: confusion of `judge_support` USE/SKIP/UNJUDGED against blind labels.
- **Arms**: contamination (above); sources read (main attempted/ok, by label, by in-run verdict; subagent and
  `judge_support` fetches apart); cost breakdown (above).
- **Extra raw data**: in-run score/verdict/tier/signals; searcher contract (record keys beyond
  `url`/`title`/`date`, dated records); read-URL overlap between arms; `useful` URLs reached only by the
  baseline; main-context peak tokens; report length; fetch failures with reasons; rounds (score_sources calls);
  skill fired.

## Limits

- One judge per question, no second rater; inter-rater agreement is not measured in the pilot (two-rater κ is
  a later option).
- One run per arm per question; agent behaviour varies run to run. Case studies, not a benchmark.
- The blind judge labels its own search pool plus whatever the arms touched; it can still disagree with a
  careful human.
- List price, not what a subscription bills.

## Pilot

20 questions: ordinals 1, 2, 3 and 6 (the expansion probe) of each mode (`make_cases.py`).
Exit criteria before the full set: labels not degenerate (no label > 90%), a fetched majority per question,
the skill fired in the with-arm, and at least one substantive disagreement per question.
