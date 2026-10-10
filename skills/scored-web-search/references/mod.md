# Running with the mod

The plugin's mod is loaded when the `score_sources` tool is available. It takes over these steps; everything else in `SKILL.md` stays the same.

| Step | Instead of | Use |
|---|---|---|
| 1. Search | a lightweight subagent | the `scored-web-search:searcher` agent. It already has `searcher-prompt.md` and is limited to WebSearch, so give it only the queries |
| 2. Score | `urls.json` + `scripts/srcscore.py` | the `score_sources` tool: `records`, `mode`, `field` when relevant, a short `round` label, and for a Step 4 re-search or Step 5 follow-up, `parentRound` set to the round it came from |
| 2.5. Judge | a judge subagent | the `judge_support` tool: `question` and every SUPPORT source. It returns `url | USE/SKIP/UNJUDGED | reason` |

If `judge_support` fails, read SUPPORT sources as before and say the judge step was unavailable. If `score_sources` fails, report it and stop; do not fall back to unscored reading.

The side view (`/search-view`) opens itself when the searcher spawns. It fills in from the tool calls above and from `WebFetch` of scored URLs, so stick to those tools; the side view only sees work done through them.
