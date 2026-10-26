# Running with the mod

The plugin's mod is loaded when the `score_sources` tool is available. It takes over these steps; everything else in `SKILL.md` stays the same.

| Step | Instead of | Use |
|---|---|---|
| 1. Search | a lightweight subagent | the `scored-web-search:searcher` agent. It already has `searcher-prompt.md` and is limited to WebSearch, so give it only the queries. Its records are kept out of your context: it hands back a batch id |
| 2. Score | the record files + `scripts/srcscore.py` | the `score_sources` tool: `batches` (the searcher batch ids; `records` for any you wrote yourself), `mode`, `field` when relevant, a short `round` label, `queries` (the keywords given to Step 1 for this round), `docVersions` / `docVariants` / `officialDomains` in place of `--doc-version` / `--doc-variant` / `--official-domain`, and for a Step 4 re-search or Step 5 follow-up, `parentRound` set to the round it came from |
| 2.5. Judge | a judge subagent | the `judge_support` tool: `question` and every SUPPORT source. It returns `url | USE/SKIP/UNJUDGED | reason` |

`judge_support` tells you when the judge could not work at all: on an error, or when every source failed to fetch, its result ends with `Judge unavailable: ...`. Follow that line; without it, every UNJUDGED line is a verdict like SKIP. If `score_sources` fails, report it and stop; do not fall back to unscored reading.

The side view (`/search-view`) opens itself when the searcher spawns. It fills in from the tool calls above and from `WebFetch` of scored URLs, so stick to those tools; the side view only sees work done through them.
