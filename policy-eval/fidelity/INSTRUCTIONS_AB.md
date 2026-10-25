# Summary-fidelity audit, WebFetch prompt A/B (judge)

You judge four WebFetch summaries of the same page against the page text. They were made with different prompts and runs. You are not told which is which, and you must not try to find out: do not open any file other than the ones listed below.

- Directory: `policy-eval/fidelity/<ID>/`. Your task message gives ID and QUESTION (the request the summaries answer).
- Read only `fetch.raw.ab.txt` (page text, starts with a `URL:` line) and `ab.S1.txt` … `ab.S4.txt`.
- Do not use web tools. Do not modify any file except the one you write in step 4.

1. **Judge each summary on its own**, against `fetch.raw.ab.txt`, with respect to QUESTION. Use the categories, verdict rules and truncation rule in `policy-eval/fidelity/INSTRUCTIONS.md` step 6 (A Omission, B Distortion, C Unsupported, D Attribution or hedge loss; "material loss", "minor loss", "faithful", "not assessable"). Count only material findings. A summary that hedges ("not found in the text read") is not penalised for that hedge; one that says "not reported" for something the page has is a B finding.
2. **False absence.** Separately count claims that something is absent from the page (not reported, not mentioned, no data) when the page has it. Each also counts as B.
3. **Verify each summary's worst finding** as in INSTRUCTIONS.md step 7: grep the page quote verbatim, under 25 words; if it fails, pick another.
4. **Report.** Write this JSON to `policy-eval/fidelity/<ID>/judge.ab.json` and reply with the same JSON only.

```json
{"id":"<ID>","summaries":{
 "S1":{"verdict":"faithful|minor loss|material loss|not assessable","counts":{"A":0,"B":0,"C":0,"D":0},"false_absence":0,
       "worst":"<category: one line>","worst_summary_quote":"","worst_page_quote":"","quote_verified":true,"notes":"<short>"},
 "S2":{...},"S3":{...},"S4":{...}}}
```
