# Summary-fidelity audit

You audit how faithful two built-in tool summaries are to their source pages: the WebSearch tool's result summary and the WebFetch tool's page summary.

- Work in `policy-eval/fidelity/<ID>/`. Create it first.
- Helper scripts are in `policy-eval/fidelity`.
- Do not use the scored-web-search skill or its tools.
- Your task message gives you ID, KIND (research | official docs), QUESTION and QUERY.

1. **Run the search.** Spawn subagent H1 with the Agent tool: subagent_type "general-purpose", model "haiku". Give it this prompt: "Call the WebSearch tool exactly once with the query: <QUERY>. Call no other tool, then reply with only DONE." Note the agentId from the Agent result.

2. **Save the search result.** Run `python3 policy-eval/fidelity/toolresult.py <H1 agentId> WebSearch policy-eval/fidelity/<ID>/websearch.txt`. If that fails, ask a new haiku agent to run the same search and reply with the complete WebSearch tool result verbatim, save that reply as websearch.txt, and set verbatim_source to "haiku_echo".

3. **Pick one primary source** from the Links in websearch.txt. It must not be a PDF.
   - research: the study or paper itself (journal article page, PMC, arXiv abs or html)
   - official docs: the official documentation page

   Check it with `python3 policy-eval/fidelity/pagetext.py <url> policy-eval/fidelity/<ID>/fetch.raw.txt`. It must give at least 3000 chars of real content. If it does not, try another link. If no link in the results qualifies, you may use a closely related primary-source URL named in the summary; say so in notes.

4. **Resolve redirects first.** WebFetch does not follow cross-host redirects (for example, www.ncbi.nlm.nih.gov/pmc to pmc.ncbi.nlm.nih.gov). Resolve the final URL with `curl -sL -o /dev/null -w '%{url_effective}' <url>` and use that URL from here on.
   **Fetch the source.** Spawn subagent H2 (general-purpose, model "haiku") with this prompt: "Call the WebFetch tool exactly once on <url>, with a prompt you write yourself to get what you need to answer this question: <QUESTION>. Call no other tool, then reply with only DONE." Then run `python3 policy-eval/fidelity/toolresult.py <H2 agentId> WebFetch policy-eval/fidelity/<ID>/webfetch.txt`. Use the same echo fallback if needed. If the result is only a redirect notice, run H2 once more on the redirect target, and say so in notes.

5. **Collect ground truth for WebSearch.** Run pagetext.py on up to 5 distinct links from websearch.txt, saving them as `policy-eval/fidelity/<ID>/search.raw1.txt` and so on.
   - Skip near-duplicates and PDFs.
   - Ignore any page with under 2000 chars of content.

6. **Compare.**
   - WebSearch: compare the summary text after the Links array with the usable pages, with respect to QUERY.
   - WebFetch: compare the result with fetch.raw.txt, with respect to the prompt H2 wrote.

   Categories:
   - **A. Omission.** Something important for the question is in a page but absent from the summary. Examples: a key number, date, version, condition or caveat, contrary finding, sample size or design.
   - **B. Distortion.** The summary states something differently from the page. Examples: a wrong number, date, version or name; a reversed meaning; overgeneralisation; sources merged into one claim; "sources agree" when they don't.
   - **C. Unsupported.** A claim in no usable page. For WebSearch, label it "maybe from an unfetched page".
   - **D. Attribution or hedge loss.** Who said it is lost (vendor vs. official, a cited study vs. the page), or a hedge or scope word is dropped (may, proposed, reportedly, in mice, in version X).

   Count only material findings. Choose a verdict:
   - "material loss": a reader relying only on the summary would hold a wrong belief about the answer.
   - "minor loss": only details or caveats are lost.
   - "faithful": no material findings.
   - "not assessable": no usable page.

   **Truncation.** WebFetch may read only the first part of a long page (it says so, for example "truncated at 100000 chars"). If the result says so, an omission located beyond that window is a window limit, not a summariser error. Record it as "beyond window" in notes and do not count it as material. A claim the result makes about the whole page, for example "X is not reported", still counts as a distortion if X is beyond the window and the result does not hedge.

7. **Verify the worst finding.** Before you report it, grep its page quote in the raw file. Set quote_verified to true only if the quote is there verbatim. Keep quotes short, under 25 words. If the worst finding fails the check, pick another.

8. **Report.** Write the JSON below to `policy-eval/fidelity/<ID>/result.json` and reply with the same JSON only. Do not include page text or prose.

```json
{"id":"<ID>","kind":"<KIND>",
 "websearch":{"verdict":"faithful|minor loss|material loss|not assessable","counts":{"A":0,"B":0,"C":0,"D":0},
   "worst":"<category: one line>","worst_summary_quote":"","worst_page_quote":"","worst_page_url":"",
   "quote_verified":true,"reader_gets_wrong":"<one sentence>","verbatim_source":"transcript|haiku_echo",
   "pages_compared":0,"notes":"<short>"},
 "webfetch":{"verdict":"...","counts":{"A":0,"B":0,"C":0,"D":0},"url":"","fetch_prompt":"",
   "worst":"","worst_summary_quote":"","worst_page_quote":"","quote_verified":true,"reader_gets_wrong":"",
   "verbatim_source":"transcript|haiku_echo","notes":""}}
```
