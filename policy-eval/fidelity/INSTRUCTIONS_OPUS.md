# Summary-fidelity audit, Opus caller (WebSearch only)

An earlier audit ran the same queries with a Haiku caller. The WebSearch tool's summary is written by the model that calls it, so this round repeats the search with an Opus caller and audits that summary. WebFetch is not repeated.

- Directory: `policy-eval/fidelity/<ID>/`. It already exists. Do not modify or delete existing files there.
- Helper scripts are in `policy-eval/fidelity`.
- Do not use the scored-web-search skill or its tools.
- Your task message gives you ID, KIND, QUESTION and QUERY.

1. **Run the search.** Spawn subagent H1 with the Agent tool: subagent_type "general-purpose", model "opus". Prompt: "Call the WebSearch tool exactly once with the query: <QUERY>. Call no other tool, then reply with only DONE." Note the agentId.
   If the Agent call fails with a concurrency or rate error, wait 60 seconds (`sleep 60` in Bash) and retry, up to 3 times.

2. **Save the result.** `python3 <fid>/toolresult.py <agentId> WebSearch <fid>/<ID>/websearch.opus.txt`. If that fails, spawn a new opus agent that runs the same search and replies with the complete WebSearch tool result verbatim; save that reply and set verbatim_source to "echo".

3. **Ground truth.** Earlier raw page texts are in `<ID>/search.raw*.txt` and `<ID>/fetch.raw.txt`; each starts with a `URL:` line. Reuse any of them whose URL is among the Links in websearch.opus.txt. For other Links, run `python3 <fid>/pagetext.py <url> <fid>/<ID>/opus.raw<N>.txt`, until you have up to 5 usable pages in total. Skip near-duplicates and PDFs; ignore pages under 2000 chars of content.

4. **Compare** the summary text after the Links array with the usable pages, with respect to QUERY. Use the categories and verdict rules in `<fid>/INSTRUCTIONS.md` step 6 (A Omission, B Distortion, C Unsupported, D Attribution or hedge loss; "material loss", "minor loss", "faithful", "not assessable"). Count only material findings.

5. **Verify the worst finding** as in INSTRUCTIONS.md step 7 (grep the page quote verbatim; under 25 words).

6. **Relate to the Haiku round.** Read `<ID>/result.json` (field `websearch`). Set `haiku_worst_repeated` to "yes" if the Opus summary makes the same error as the Haiku round's worst finding, "no" if it states that point correctly, "na" if it does not touch that point.

7. **Report.** Write this JSON to `<fid>/<ID>/result.opus.json` and reply with the same JSON only. No page text or prose.

```json
{"id":"<ID>","kind":"<KIND>","caller":"opus",
 "verdict":"faithful|minor loss|material loss|not assessable","counts":{"A":0,"B":0,"C":0,"D":0},
 "worst":"<category: one line>","worst_summary_quote":"","worst_page_quote":"","worst_page_url":"",
 "quote_verified":true,"reader_gets_wrong":"<one sentence>","verbatim_source":"transcript|echo",
 "pages_compared":0,"pages_reused":0,"links_total":0,"links_shared_with_haiku_round":0,
 "haiku_worst_repeated":"yes|no|na","notes":"<short>"}
```

`<fid>` = `policy-eval/fidelity`.
