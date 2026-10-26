# Evaluation Methodology

Two questions, measured on the same questions:

- **A — policy.** Do `scripts/policy.json` + `scripts/modes/*.json` verdicts agree with an independent judge?
- **B — Step 2.5 judge.** Do the judge subagent's USE/SKIP decisions agree with the same judge?
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
   `runs/<id>.judge.json` (with `judged_at`, the run date) and `runs/<id>.urls.json` (URL/title/search-result
   date). These two are tracked: they are the regression set (see "Run log").
   `expansion: true` questions are judged in two rounds: round-2 keywords come only from the body text of
   round-1 pages the judge fetched, recorded as `expansion_keywords` `{keyword, source_url}`; entries carry `round`.
2. **Arms** (`claude plugin eval`, one case per question, `policy-eval/arms/<id>/case.yaml`, Opus 5.5).
   The same prompt runs twice: with the plugin from this repo, and with no plugin (temp HOME/config, no
   CLAUDE.md in either arm). The prompt names "scored web search" only as words, so the baseline is an
   ordinary research agent. `tool_used: Skill` records whether the skill fired in the with-arm.
   Arms measure the skill working as intended, so the run grants what a real session needs. Eval Bash runs in
   a sandbox whose network opens only to hosts named in a `WebFetch(domain:…)` grant (a bare `WebFetch` grant
   does not open it), and without the scorer's hosts `srcscore.py` exits 4. `--keep-temp` keeps the traces
   `arms.py ingest` copies:

       claude plugin eval . --eval-dir policy-eval/arms --keep-temp --allow-tools Bash Write WebSearch WebFetch \
         "WebFetch(domain:api.openalex.org)" "WebFetch(domain:api.semanticscholar.org)" \
         "WebFetch(domain:api.github.com)" "WebFetch(domain:hn.algolia.com)"
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
| `listed` | only the URL/title is in the main context: a search result with no summary, a score-table row, or a Step 1 record a search subagent handed back inline |
| `sub_read` | a subagent fetched it; nothing of it is in the main context |
| `sub_seen` | only a subagent's search returned it |

**Contamination** = `marginal` + `junk` URLs at a main-context level (`read`, `exposed`, `listed`), reported
per level × label × tier. The tier comes from scoring every URL with the current policy, in both arms, so the
baseline's sources are placed on the same ladder.

## Cost

USD at list price (`pricing.py`). Sources, most authoritative first:

- `result.modelUsage` (per model, every API call of the run, including tools' internal calls and web-search fees).
- Session transcripts (`config/projects/<cwd>/<session>.jsonl` and `subagents/agent-*.jsonl` in the kept
  sandbox): final per-message `usage` → cost per agent role (`main`, `subagent`). The stream trace's
  per-message usage is interim and is not used for cost.
- `tool_internal_usd` per model = `modelUsage.costUSD` − transcript cost − web searches × $0.01. It is the
  model calls the WebSearch/WebFetch tools make on their own (WebSearch runs on the caller's model,
  WebFetch's summariser on Haiku).

## Measures

- **A**: recall loss (`useful` scored below SUPPORT), precision loss (`junk` scored PRIMARY/SUPPORT), vendor
  pass-through, per-round split for expansion questions (`contrast.py`).
- **B**: confusion of the judge's USE/SKIP/UNJUDGED (UNJUDGED split by `page`/`fetch-error`) against blind labels
  (`judge_vs_blind`).
- **Arms**: contamination (above); sources read (main attempted/ok, by label; subagent fetches apart); cost
  breakdown (above).
- **Pipeline compliance** (with-arm): the main agent's reads by the verdict the run acted on
  (`main_reads_by_in_run_verdict`: `PRIMARY`, `SUPPORT/USE`, … or `not-in-table`; anything but PRIMARY and
  `SUPPORT/USE`, or `SUPPORT` when the judge was unavailable, is a read the skill forbids); the report's
  `Degraded:` line held against the same items counted from the trace (`degraded`); scorer exit codes.
- **Extra raw data**: read-URL overlap between arms; `useful` URLs reached only by the baseline; main-context
  peak tokens; characters the score tables and subagent replies put into the main context
  (`main_context_chars`); report length; fetch failures with reasons; skill fired.

`arms.py` reads what the skill's own steps leave in a trace: the `srcscore.py` Bash results (rows, counts,
unreachable lookups, exit), the URL files subagents write, each subagent's reply to the main agent (a URL-file
path and count, inline records, or judge lines; taken from the subagent's last text, so a background subagent
whose reply came as a task notification counts the same), and the report's closing summary and `Degraded:` line.

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

## Summary fidelity

How faithful the built-in tool summaries the agent reads are to their pages: `fidelity/`. Page text comes from
`pagetext.py` (curl, no model); verbatim tool results come from subagent transcripts (`toolresult.py`). Neither
raw file is tracked. Categories and verdict rules are in `fidelity/INSTRUCTIONS.md` step 6, and the worst finding
of every summary is grep-verified against the page (step 7).

- **Rounds 1-2** (`result.json`, `result.opus.json`): WebSearch with a Haiku and an Opus caller, and WebFetch,
  30 topics. Single run per topic, one judge that knew the round.
- **WebFetch prompt A/B** (`result.ab.json`, `INSTRUCTIONS_AB.md`, `ab.py`). Does the SKILL.md Step 3 fidelity
  block change what the Haiku summariser loses?
  - *Arms.* B = the round-1 `fetch_prompt` verbatim; T = the same prompt + the block (`ab.py` BLOCK, the text SKILL.md
    Step 3 carried during the run). Only the prompt differs.
  - *Runs.* One Haiku agent per topic makes four WebFetch calls, B T B T, on the round-1 URL. Same URL inside the
    tool's 15-minute cache, so all four summarise the same fetch. B is re-run rather than reused from round 1, so
    page and model drift hit both arms alike.
  - *Ground truth.* The page re-fetched right after (`fetch.raw.ab.txt`).
  - *Blinding.* The four summaries are written as `ab.S1`-`ab.S4` in a per-topic hash order; the arm map
    (`ab.map.json`) is not shown to the judge. One Sonnet judge per topic sees the page, the four summaries and
    the baseline request only: not the block, the arms or earlier results. The block's wording ("not found in
    the text read", "inference:") can still reveal T to an attentive judge.
  - *Measures.* Per arm: verdicts, A-D counts, false absences (the page has what the summary says is absent),
    mean summary length.
  - *Decision rule, fixed before the run.* Per topic, loss = material 2, minor 1, faithful 0, summed over the
    two runs. T is called better only if the paired sign test of B−T is in T's favour at p < 0.05 **and** the
    effect is larger than the within-arm run1−run2 split (`noise_run1_vs_run2`); otherwise "not distinguishable".
  - *Result (2026-10).* Not distinguishable: paired 7 topics for T, 6 for B (p = 1.0); B's own run1−run2 split
    was 5:1. Material 1 (B) vs 0 (T), false absence 1 vs 0, T summaries 25% longer. The block was removed from
    SKILL.md.
  - *Limits.* n = 30, one judge per topic, two runs per arm. The baseline prompts were written by a Haiku caller;
    an Opus main agent writes different ones. The judge's truncation calls depend on the page text, which can be
    longer than what WebFetch read.

## Environment probes

`policy-eval/probe/` holds short cases that check what the skill's steps can do inside an eval run, apart from any
research: the scorer's file and network access (`p1-score`), where a subagent can write the URL file that sandboxed
Bash then reads (`p2-urlfile`), and the Step 2.5 judge subagent's WebFetch and reply format (`p3-judge`). Each ends
its reply with `PROBE key=value` lines. Run them with `--ablation none --keep-temp` and the grants under test, and
read the replies from the kept traces. Re-run them when the skill's steps or the eval build change.

## Run log

Dates are when each round ran. Labels age: pages change and "current version" questions go stale, so read a
result against its date.

| Date | Round | Output |
|---|---|---|
| 2026-10-24 | Blind judge, 20 pilot questions | `runs/*.judge.json`, `runs/*.urls.json`, `results/a_summary.json` |
| 2026-10-24 | Arms, with vs without. The Step 2.5 judge of that version fetched pages from plugin code, which `claude plugin eval` refuses, so it never judged; the current judge is a subagent that reads with WebFetch | `results/pilot.summary.json` |
| 2026-10-25 | Fidelity round 1 (Haiku caller) and round 2 (Opus caller WebSearch) | `fidelity/*/result.json`, `result.opus.json` |
| 2026-10-25 | WebFetch prompt A/B | `fidelity/*/result.ab.json`, `summary.json` `webfetch_ab` |
| 2026-10-25 | URL path rules re-scored against the 2026-10-24 labels (contrast) | not tracked; regenerate with `contrast.py` |
| 2026-10-25 | Issue #5 overlay changes re-scored against the 2026-10-24 labels (contrast, in-sample: candidates were picked from these labels) | not tracked; numbers in `handoff/be76cb7418ff8904-pending-evals.md` E2 |
| 2026-10-26 | Environment probes (`policy-eval/probe/`). Bash reads the loaded skill's folder but not the rest of the plugin (`policy-eval/` is denied); scorer hosts reachable only with `WebFetch(domain:…)` grants; `/tmp` neither writable nor readable, `$TMPDIR` and the working directory both; judge subagent given only `judge-rubric.md` fetched, answered one line per URL, tagged UNJUDGED as `page:` (PDF, 404) or `fetch-error:` (unresolvable host), and re-read only the `fetch-error` URL once | not tracked; summary in `handoff/be76cb7418ff8904-pending-evals.md` |
| 2026-10-26 | Scorer under the eval proxy (`p4-score-load`) and ac1 with-arm end to end. The proxy cut 1-3 of 12 serial OpenAlex replies mid-body, which crashed `srcscore.py` (`IncompleteRead`) on a real Step 1 pool; it now retries a cut-off reply once and counts a second failure as unreachable | not tracked |

The tracked labels are a regression set: re-run `contrast.py` after a policy change to see what it newly passes
or drops. They are not where new weaknesses are found; tuning to 20 questions overfits them. Finding gaps takes
new questions, judged in a new dated round.
