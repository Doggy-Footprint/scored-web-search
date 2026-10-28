"""Independent tests for the with/without-plugin arms evaluation code.

Written from the specification (the task brief and policy-eval/METHODOLOGY.md), not from the
implementation. Expected values are computed by hand from the stated rates and rules. Under test:

A. policy-eval/pricing.py     -- list-price USD for an API `usage` dict, aliases, Haiku long prompt.
B. policy-eval/arms.py        -- URL normalisation, WebSearch parsing, trace extraction,
                                 session-transcript cost, exposure levels, cost breakdown,
                                 ingest and per-arm analysis.
C. policy-eval/make_cases.py  -- the 20-question pilot and the generated case.yaml.
D. policy-eval/contrast.py    -- prefers runs/<id>.urls.json over runs/<id>.urls.txt.

Nothing is written into the repo: arms.RUNS is monkeypatched to a temp dir, make_cases.py and contrast.py run from temp mirrors. No test needs the network: the only
scorer run (D) uses domains that resolve from the tier table alone (see test_expansion_eval.py).
"""

import contextlib
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

import _pathsetup  # noqa: F401  (sys.path bootstrap for scripts/)

ROOT = _pathsetup.ROOT
SCRIPTS = _pathsetup.SCRIPTS
POLICY_EVAL = os.path.join(ROOT, "policy-eval")
if POLICY_EVAL not in sys.path:
    sys.path.insert(0, POLICY_EVAL)

import arms  # noqa: E402
import pricing  # noqa: E402

M = 1_000_000
SEARCH_AGENT = "Explore"


# --------------------------------------------------------------------------- A pricing

class TestPricingRates(unittest.TestCase):

    def one(self, model, **usage):
        return pricing.usage_usd(model, usage)

    def test_opus_each_rate(self):
        self.assertAlmostEqual(self.one("claude-opus-5-5", input_tokens=M), 4.0)
        self.assertAlmostEqual(self.one("claude-opus-5-5", output_tokens=M), 20.0)
        self.assertAlmostEqual(self.one("claude-opus-5-5", cache_read_input_tokens=M), 0.20)
        self.assertAlmostEqual(pricing.usage_usd("claude-opus-5-5", {
            "cache_creation": {"ephemeral_5m_input_tokens": M, "ephemeral_1h_input_tokens": 0}}), 5.0)
        self.assertAlmostEqual(pricing.usage_usd("claude-opus-5-5", {
            "cache_creation": {"ephemeral_5m_input_tokens": 0, "ephemeral_1h_input_tokens": M}}), 8.0)

    def test_sonnet_each_rate(self):
        self.assertAlmostEqual(self.one("claude-sonnet-5-5", input_tokens=M), 2.0)
        self.assertAlmostEqual(self.one("claude-sonnet-5-5", output_tokens=M), 10.0)
        self.assertAlmostEqual(self.one("claude-sonnet-5-5", cache_read_input_tokens=M), 0.20)
        self.assertAlmostEqual(pricing.usage_usd("claude-sonnet-5-5", {
            "cache_creation": {"ephemeral_5m_input_tokens": M}}), 2.5)
        self.assertAlmostEqual(pricing.usage_usd("claude-sonnet-5-5", {
            "cache_creation": {"ephemeral_1h_input_tokens": M}}), 4.0)

    def test_haiku_short_prompt_rates(self):
        # 10K-token pieces keep every case well below the 100K long-prompt threshold.
        n = 10_000
        self.assertAlmostEqual(self.one("claude-haiku-5-5", input_tokens=n), n * 0.10 / M)
        self.assertAlmostEqual(self.one("claude-haiku-5-5", output_tokens=n), n * 0.50 / M)
        self.assertAlmostEqual(self.one("claude-haiku-5-5", cache_read_input_tokens=n), n * 0.01 / M)
        self.assertAlmostEqual(pricing.usage_usd("claude-haiku-5-5", {
            "cache_creation": {"ephemeral_5m_input_tokens": n}}), n * 0.125 / M)
        self.assertAlmostEqual(pricing.usage_usd("claude-haiku-5-5", {
            "cache_creation": {"ephemeral_1h_input_tokens": n}}), n * 0.20 / M)

    def test_mixed_usage_sums_all_parts(self):
        u = {"input_tokens": 1000, "output_tokens": 500, "cache_read_input_tokens": 10000,
             "cache_creation": {"ephemeral_5m_input_tokens": 2000, "ephemeral_1h_input_tokens": 3000}}
        want = (1000 * 4 + 500 * 20 + 10000 * 0.20 + 2000 * 5 + 3000 * 8) / M
        self.assertAlmostEqual(pricing.usage_usd("claude-opus-5-5", u), want)

    def test_missing_fields_count_as_zero(self):
        self.assertEqual(pricing.usage_usd("claude-opus-5-5", {}), 0)


class TestPricingCacheWrites(unittest.TestCase):

    def test_flat_cache_creation_is_five_minute_when_no_breakdown(self):
        self.assertAlmostEqual(
            pricing.usage_usd("claude-opus-5-5", {"cache_creation_input_tokens": M}), 5.0)
        self.assertAlmostEqual(
            pricing.usage_usd("claude-sonnet-5-5", {"cache_creation_input_tokens": M}), 2.5)

    def test_breakdown_wins_over_flat_count(self):
        """When the breakdown is present the flat total must not be billed a second time."""
        u = {"cache_creation_input_tokens": 2 * M,
             "cache_creation": {"ephemeral_5m_input_tokens": M, "ephemeral_1h_input_tokens": M}}
        self.assertAlmostEqual(pricing.usage_usd("claude-opus-5-5", u), 5.0 + 8.0)

    def test_one_hour_breakdown_only(self):
        u = {"cache_creation_input_tokens": M,
             "cache_creation": {"ephemeral_5m_input_tokens": 0, "ephemeral_1h_input_tokens": M}}
        self.assertAlmostEqual(pricing.usage_usd("claude-opus-5-5", u), 8.0)


class TestPricingHaikuLongPrompt(unittest.TestCase):

    def test_exactly_threshold_is_short(self):
        self.assertAlmostEqual(
            pricing.usage_usd("claude-haiku-5-5", {"input_tokens": 100_000}), 100_000 * 0.10 / M)

    def test_one_over_threshold_is_long(self):
        self.assertAlmostEqual(
            pricing.usage_usd("claude-haiku-5-5", {"input_tokens": 100_001}), 100_001 * 0.50 / M)

    def test_threshold_counts_input_cache_read_and_cache_writes(self):
        u = {"input_tokens": 50_000, "cache_read_input_tokens": 30_000, "output_tokens": 1_000,
             "cache_creation": {"ephemeral_5m_input_tokens": 15_000, "ephemeral_1h_input_tokens": 5_001}}
        want = (50_000 * 0.50 + 30_000 * 0.05 + 1_000 * 2.50 + 15_000 * 0.625 + 5_001 * 1.00) / M
        self.assertAlmostEqual(pricing.usage_usd("claude-haiku-5-5", u), want)

    def test_flat_cache_creation_counts_towards_threshold(self):
        u = {"input_tokens": 60_000, "cache_creation_input_tokens": 40_001}
        want = (60_000 * 0.50 + 40_001 * 0.625) / M
        self.assertAlmostEqual(pricing.usage_usd("claude-haiku-5-5", u), want)

    def test_output_does_not_count_towards_threshold(self):
        u = {"input_tokens": 100_000, "output_tokens": 50_000}
        self.assertAlmostEqual(pricing.usage_usd("claude-haiku-5-5", u),
                               (100_000 * 0.10 + 50_000 * 0.50) / M)

    def test_long_rule_is_haiku_only(self):
        self.assertAlmostEqual(pricing.usage_usd("claude-opus-5-5", {"input_tokens": 200_000}),
                               200_000 * 4 / M)
        self.assertAlmostEqual(pricing.usage_usd("claude-sonnet-5-5", {"input_tokens": 200_000}),
                               200_000 * 2 / M)


class TestPricingModels(unittest.TestCase):

    def test_aliases(self):
        for alias, full in (("opus", "claude-opus-5-5"), ("sonnet", "claude-sonnet-5-5"),
                            ("haiku", "claude-haiku-5-5")):
            with self.subTest(alias=alias):
                self.assertEqual(pricing.canonical(alias), full)
                self.assertAlmostEqual(pricing.usage_usd(alias, {"input_tokens": 1000}),
                                       pricing.usage_usd(full, {"input_tokens": 1000}))

    def test_suffixed_ids_resolve(self):
        for mid, full in (("claude-opus-5-5[1m]", "claude-opus-5-5"),
                          ("claude-opus-5-5-xyz", "claude-opus-5-5"),
                          ("claude-haiku-5-5-20261001", "claude-haiku-5-5"),
                          ("claude-sonnet-5-5[1m]", "claude-sonnet-5-5")):
            with self.subTest(model=mid):
                self.assertEqual(pricing.canonical(mid), full)
                self.assertAlmostEqual(pricing.usage_usd(mid, {"output_tokens": M}),
                                       pricing.usage_usd(full, {"output_tokens": M}))

    def test_unknown_model_raises_keyerror(self):
        for mid in ("gpt-5", "claude-opus-4-1", "luna", ""):
            with self.subTest(model=mid):
                with self.assertRaises(KeyError):
                    pricing.usage_usd(mid, {"input_tokens": 1})

    def test_canonical_none_for_unknown_or_empty(self):
        for mid in ("gpt-5", "luna", "", None):
            with self.subTest(model=mid):
                self.assertIsNone(pricing.canonical(mid))

    def test_web_search_price(self):
        self.assertEqual(pricing.WEB_SEARCH_USD, 0.01)


# --------------------------------------------------------------------------- B arms: parsers

class TestNorm(unittest.TestCase):

    def test_lowercases_scheme_and_host_strips_www(self):
        self.assertEqual(arms.norm("HTTPS://WWW.Example.COM/Path/Doc"), "https://example.com/Path/Doc")

    def test_path_case_kept(self):
        self.assertEqual(arms.norm("https://example.com/AbC"), "https://example.com/AbC")

    def test_drops_fragment(self):
        self.assertEqual(arms.norm("https://example.com/a#section-2"), "https://example.com/a")

    def test_trailing_slash_and_empty_path(self):
        self.assertEqual(arms.norm("https://example.com/a/"), "https://example.com/a")
        self.assertEqual(arms.norm("https://example.com"), "https://example.com/")
        self.assertEqual(arms.norm("https://example.com/"), "https://example.com/")

    def test_drops_utm_keeps_other_params_in_order(self):
        self.assertEqual(arms.norm("https://example.com/a?b=2&utm_source=x&a=1&utm_medium=y"),
                         "https://example.com/a?b=2&a=1")

    def test_only_utm_params_leaves_no_query(self):
        self.assertEqual(arms.norm("https://example.com/a?utm_source=x&utm_campaign=z"),
                         "https://example.com/a")

    def test_strips_trailing_punctuation(self):
        for tail in (".", ",", ";"):
            with self.subTest(tail=tail):
                self.assertEqual(arms.norm("https://example.com/a" + tail), "https://example.com/a")

    def test_variants_join_to_one_key(self):
        variants = ["https://www.example.org/x/", "https://example.org/x",
                    "HTTPS://EXAMPLE.ORG/x#top", "https://example.org/x?utm_source=feed",
                    "https://example.org/x/."]
        self.assertEqual(len({arms.norm(v) for v in variants}), 1)

    def test_idempotent(self):
        u = arms.norm("https://WWW.example.com/a/?q=1&utm_x=2#f")
        self.assertEqual(arms.norm(u), u)


class TestSearchLinks(unittest.TestCase):

    def test_links_and_summary(self):
        text = ('Web search results for query: "q"\n\n'
                'Links: [{"title":"A","url":"https://a.org/1"},{"title":"B","url":"https://b.org/2"}]\n\n'
                '  A summary of the results.  \n')
        links, rest = arms.search_links(text)
        self.assertEqual([l["url"] for l in links], ["https://a.org/1", "https://b.org/2"])
        self.assertEqual(links[0].get("title"), "A")
        self.assertEqual(rest, "A summary of the results.")

    def test_links_without_summary(self):
        links, rest = arms.search_links('Links: [{"title":"A","url":"https://a.org/1"}]')
        self.assertEqual([l["url"] for l in links], ["https://a.org/1"])
        self.assertEqual(rest, "")

    def test_links_no_space_after_colon(self):
        links, rest = arms.search_links('Links:[{"title":"A","url":"https://a.org/1"}] tail')
        self.assertEqual([l["url"] for l in links], ["https://a.org/1"])
        self.assertEqual(rest, "tail")

    def test_every_link_has_url(self):
        links, _ = arms.search_links('Links: [{"title":"A","url":"https://a.org/1"},{"title":"no url"}]')
        self.assertTrue(all(l.get("url") for l in links))
        self.assertIn("https://a.org/1", [l["url"] for l in links])

    def test_no_links_marker(self):
        self.assertEqual(arms.search_links("no results here https://a.org/1"), ([], ""))
        self.assertEqual(arms.search_links(""), ([], ""))


# --------------------------------------------------------------------------- B arms: fixtures

# URLs in the "with" fixture and the level each must reach (norm form). See WithFixture.
U = {
    "A": "https://www.alpha.org/read-and-exposed/",       # main search w/ summary + main fetch ok
    "B": "https://bravo.org/exposed",                     # main search w/ summary
    "C": "https://charlie.org/listed",                    # main search, no summary
    "D": "https://delta.org/sub-seen",                    # search subagent's own search only
    "J": "https://juliet.org/sub-fetch",                  # other subagent fetch ok
    "K": "https://kilo.org/fetch-error",                  # main fetch is_error
    "L": "https://lima.org/listed-then-404",              # main search no summary + main fetch 404
    "M": "https://mike.org/fetch-no-code",                # main fetch ok, no code
    "N": "https://november.org/fetch-empty",              # main fetch 200, empty text
    "O": "https://oscar.org/fetch-301",                   # main fetch 301
    "P": "https://papa.org/sub-seen-and-exposed",         # subagent search + main search w/ summary
    "Q": "https://quebec.org/other-agent-json",           # only in a subagent's reply text
}

EXPECTED_LEVELS = {
    "A": "read", "B": "exposed", "C": "listed", "D": "sub_seen", "J": "sub_read", "L": "listed",
    "M": "read", "O": "read", "P": "exposed",
}
ABSENT = ("K", "N", "Q")
# The union todo lists only URLs that reached the main agent's context in some arm.
MAIN_CONTEXT_KEYS = tuple(k for k, lvl in EXPECTED_LEVELS.items() if lvl in ("listed", "exposed", "read"))

SESSION = "sess-with-0001"


def links_text(urls, summary=""):
    links = [{"title": "T " + k, "url": U[k]} for k in urls]
    return 'Web search results for query: "x"\n\nLinks: ' + json.dumps(links) + (
        "\n\n" + summary if summary else "")


def a_msg(mid, parent, content, model="claude-opus-5-5", usage=None):
    return {"type": "assistant", "parent_tool_use_id": parent,
            "message": {"id": mid, "model": model, "content": content,
                        "usage": usage or {"input_tokens": 1, "output_tokens": 1}}}


def tool_use(tid, name, inp):
    return {"type": "tool_use", "id": tid, "name": name, "input": inp}


def result_msg(parent, tid, content, tur=None, is_error=None):
    block = {"type": "tool_result", "tool_use_id": tid, "content": content}
    if is_error is not None:
        block["is_error"] = is_error
    d = {"type": "user", "parent_tool_use_id": parent, "message": {"content": [block]}}
    if tur is not None:
        d["tool_use_result"] = tur
    return d


SEARCH_REPLY = "Wrote 2 records to urls-1.json"

# modelUsage: costUSD = transcript cost + web searches x $0.01 + an internal remainder.
#   opus   transcript 0.0342 + 2 searches 0.02 + internal 0.0010 = 0.0552
#   haiku  transcript 0.0015 + 1 search   0.01 + internal 0.0005 = 0.0120
#   sonnet transcript 0.0060 + 0 searches       + internal 0      = 0.0060
MODEL_USAGE = {
    "claude-opus-5-5": {"costUSD": 0.0552, "webSearchRequests": 2, "inputTokens": 1},
    "claude-haiku-5-5": {"costUSD": 0.0120, "webSearchRequests": 1},
    "claude-sonnet-5-5": {"costUSD": 0.0060, "webSearchRequests": 0},
}


def with_trace_lines():
    L = [{"type": "system", "subtype": "init", "session_id": SESSION}]
    L.append(a_msg("m0", None, [tool_use("sk1", "Skill", {"skill": "scored-web-search"})]))
    L.append(result_msg(None, "sk1", "skill loaded"))
    # main WebSearch with summary -> exposed
    L.append(a_msg("m1", None, [tool_use("ws1", "WebSearch", {"query": "main query one"})]))
    L.append(result_msg(None, "ws1", links_text(["A", "B", "P"], "Summary of the results.")))
    # main WebSearch, no summary -> listed
    L.append(a_msg("m2", None, [tool_use("ws2", "WebSearch", {"query": "main query two"})]))
    L.append(result_msg(None, "ws2", [{"type": "text", "text": links_text(["C", "L"])}]))
    # search subagent
    L.append(a_msg("m3", None, [tool_use("ag1", "Agent", {"subagent_type": SEARCH_AGENT, "prompt": "go"})]))
    L.append({"type": "system", "subtype": "task_started", "tool_use_id": "ag1",
              "subagent_type": SEARCH_AGENT, "description": "search round 1"})
    L.append(a_msg("s1", "ag1", [tool_use("ws3", "WebSearch", {"query": "sub query"})],
                   model="claude-haiku-5-5"))
    L.append(result_msg("ag1", "ws3", links_text(["D", "P"], "sub summary")))
    L.append(result_msg(None, "ag1", [{"type": "text", "text": SEARCH_REPLY}],
                        tur={"agentId": "A1", "agentType": SEARCH_AGENT, "status": "completed"}))
    # another subagent: fetches J, and replies with JSON whose URL must not count
    L.append(a_msg("m4", None, [tool_use("ag2", "Agent", {"subagent_type": "general-purpose"})]))
    L.append({"type": "system", "subtype": "task_started", "tool_use_id": "ag2",
              "subagent_type": "general-purpose", "description": "helper"})
    L.append(a_msg("g1", "ag2", [tool_use("wfs", "WebFetch", {"url": U["J"], "prompt": "p"})],
                   model="claude-sonnet-5-5"))
    L.append(result_msg("ag2", "wfs", "sub page text", tur={"code": 200, "bytes": 1234}))
    L.append(result_msg(None, "ag2", json.dumps([{"url": U["Q"], "title": "Q"}]),
                        tur={"agentId": "A2", "agentType": "general-purpose"}))
    # main fetches, two tool_uses in one message
    L.append(a_msg("m7", None, [tool_use("wf1", "WebFetch", {"url": U["A"]}),
                                 tool_use("wf2", "WebFetch", {"url": U["K"]})]))
    L.append(result_msg(None, "wf1", "page A text", tur={"code": 200, "bytes": 10}))
    L.append(result_msg(None, "wf2", "Error: boom", tur={"code": 200, "bytes": 0}, is_error=True))
    L.append(a_msg("m8", None, [tool_use("wf3", "WebFetch", {"url": U["L"]})]))
    L.append(result_msg(None, "wf3", "Not Found", tur={"code": 404, "bytes": 9}))
    L.append(a_msg("m9", None, [tool_use("wf4", "WebFetch", {"url": U["M"]})]))
    L.append(result_msg(None, "wf4", [{"type": "text", "text": "page M text"}]))
    L.append(a_msg("m10", None, [tool_use("wf5", "WebFetch", {"url": U["N"]})]))
    L.append(result_msg(None, "wf5", "   ", tur={"code": 200, "bytes": 0}))
    L.append(a_msg("m11", None, [tool_use("wf6", "WebFetch", {"url": U["O"]})]))
    L.append(result_msg(None, "wf6", "moved page text", tur={"code": 301, "bytes": 5}))
    L.append({"type": "result", "subtype": "success", "result": "REPORT", "total_cost_usd": 0.0732,
              "modelUsage": MODEL_USAGE, "num_turns": 12})
    return L


# Session transcripts. Repeated message ids carry interim usage first; the last line is final.
#   main (opus)  m1 final: in 1000 out 500 cr 10000 cc 2000 -> 4000+10000+2000+10000 = 0.0260
#                m2 final: in 50 out 100 cr 30000           -> 200+2000+6000       = 0.0082
#                -> 0.0342, peak context = max(13000, 30050) = 30050
#   search (haiku)   s1 final: in 10000 out 1000 -> 1000 + 500 = 0.0015
#   other (sonnet)   g1 final: in 2000 out 200   -> 4000 + 2000 = 0.0060
MAIN_SESSION = [
    {"type": "user", "message": {"content": "hi"}},
    a_msg("m1", None, [], usage={"input_tokens": 1, "output_tokens": 1}),
    a_msg("m1", None, [], usage={"input_tokens": 1000, "output_tokens": 500,
                                 "cache_read_input_tokens": 10000, "cache_creation_input_tokens": 2000}),
    a_msg("m2", None, [], usage={"input_tokens": 99999, "output_tokens": 9,
                                 "cache_read_input_tokens": 99999}),
    a_msg("m2", None, [], usage={"input_tokens": 50, "output_tokens": 100,
                                 "cache_read_input_tokens": 30000}),
]
SEARCH_SESSION = [
    a_msg("s1", None, [], model="claude-haiku-5-5", usage={"input_tokens": 1, "output_tokens": 0}),
    a_msg("s1", None, [], model="claude-haiku-5-5", usage={"input_tokens": 10000, "output_tokens": 1000}),
]
OTHER_SESSION = [
    a_msg("g1", None, [], model="claude-sonnet-5-5", usage={"input_tokens": 2000, "output_tokens": 200}),
]


def write_jsonl(path, lines):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as fh:
        for d in lines:
            fh.write(json.dumps(d) + "\n")


def build_run(root, trace_lines, session=None, main=None, subagents=None):
    """<root>/trace/trace.jsonl plus <root>/config/projects/<cwd>/<session>.jsonl (+ subagents/)."""
    trace = os.path.join(root, "trace", "trace.jsonl")
    write_jsonl(trace, trace_lines)
    if session and main is not None:
        proj = os.path.join(root, "config", "projects", "-private-tmp-sandbox-cwd")
        write_jsonl(os.path.join(proj, session + ".jsonl"), main)
        for agent_id, lines in (subagents or {}).items():
            write_jsonl(os.path.join(proj, session, "subagents", "agent-%s.jsonl" % agent_id), lines)
    return trace


class WithFixture(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp(prefix="arms-eval-")
        cls.trace = build_run(os.path.join(cls.tmp, "with"), with_trace_lines(), SESSION, MAIN_SESSION,
                              {"A1": SEARCH_SESSION, "A2": OTHER_SESSION})
        cls.ev = arms.extract(cls.trace)

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)


# --------------------------------------------------------------------------- B arms: extract

class TestExtractEvents(WithFixture):

    def test_searches(self):
        s = self.ev["searches"]
        self.assertEqual([(x["role"], x["query"]) for x in s],
                         [("main", "main query one"), ("main", "main query two"), (SEARCH_AGENT, "sub query")])
        self.assertEqual([l["url"] for l in s[0]["links"]], [U["A"], U["B"], U["P"]])
        self.assertGreater(s[0]["summary_chars"], 0)
        self.assertEqual(s[1]["summary_chars"], 0)
        self.assertEqual([l["url"] for l in s[1]["links"]], [U["C"], U["L"]])
        self.assertGreater(s[2]["summary_chars"], 0)

    def test_fetches_ok_rule_and_roles(self):
        got = {(f["role"], f["url"]): (f["ok"], f["code"]) for f in self.ev["fetches"]}
        self.assertEqual(got, {
            ("general-purpose", U["J"]): (True, 200),
            ("main", U["A"]): (True, 200),
            ("main", U["K"]): (False, 200),    # is_error
            ("main", U["L"]): (False, 404),    # 4xx
            ("main", U["M"]): (True, None),    # no code, non-empty text
            ("main", U["N"]): (False, 200),    # empty text
            ("main", U["O"]): (True, 301),     # 3xx counts as ok
        })

    def test_skill_fired_and_report_chars(self):
        self.assertIs(self.ev["skill_fired"], True)
        self.assertEqual(self.ev["report_chars"], len("REPORT"))

    def test_skill_not_fired_without_skill_tool_use(self):
        lines = [l for l in with_trace_lines()
                 if not (l["type"] == "assistant" and l["message"]["id"] == "m0")]
        root = os.path.join(self.tmp, "noskill")
        ev = arms.extract(build_run(root, lines))
        self.assertIs(ev["skill_fired"], False)


class TestExtractSessionUsage(WithFixture):

    def rows(self):
        return {r["role"]: r for r in self.ev["usage_by_role"]}

    def test_roles_mapped_through_agent_id(self):
        self.assertEqual(sorted(self.rows()), sorted(["main", SEARCH_AGENT, "general-purpose"]))
        self.assertEqual(len(self.ev["usage_by_role"]), 3)

    def test_models(self):
        r = self.rows()
        self.assertEqual(r["main"]["model"], "claude-opus-5-5")
        self.assertEqual(r[SEARCH_AGENT]["model"], "claude-haiku-5-5")
        self.assertEqual(r["general-purpose"]["model"], "claude-sonnet-5-5")

    def test_last_line_per_message_id_is_final(self):
        m = self.rows()["main"]
        self.assertEqual(m["requests"], 2)
        self.assertEqual(m["input_tokens"], 1050)
        self.assertEqual(m["output_tokens"], 600)
        self.assertEqual(m["cache_read_input_tokens"], 40000)
        self.assertEqual(m["cache_creation_input_tokens"], 2000)
        self.assertAlmostEqual(m["usd"], 0.0342)

    def test_subagent_usd(self):
        r = self.rows()
        self.assertAlmostEqual(r[SEARCH_AGENT]["usd"], 0.0015)
        self.assertEqual(r[SEARCH_AGENT]["requests"], 1)
        self.assertAlmostEqual(r["general-purpose"]["usd"], 0.0060)

    def test_cost_not_from_trace_usage(self):
        """The trace's own per-message usage (1 in / 1 out each) must not be what is summed."""
        self.assertNotEqual(self.rows()["main"]["input_tokens"], 12)

    def test_main_peak_context(self):
        self.assertEqual(self.ev["main_peak_context"], 30050)


# --------------------------------------------------------------------------- B arms: levels

class TestArmUrls(WithFixture):

    def test_levels(self):
        lvl = arms.arm_urls(self.ev)
        for k, want in EXPECTED_LEVELS.items():
            with self.subTest(url=k):
                self.assertEqual(lvl.get(arms.norm(U[k])), want)

    def test_absent(self):
        lvl = arms.arm_urls(self.ev)
        for k in ABSENT:
            with self.subTest(url=k):
                self.assertNotIn(arms.norm(U[k]), lvl)

    def test_keys_are_normalised(self):
        lvl = arms.arm_urls(self.ev)
        self.assertEqual(set(lvl), {arms.norm(U[k]) for k in EXPECTED_LEVELS})
        self.assertIn("https://alpha.org/read-and-exposed", lvl)

    def synth(self, **parts):
        ev = {"searches": [], "fetches": []}
        ev.update(parts)
        return arms.arm_urls(ev)

    def test_order_is_independent_of_event_order(self):
        """Highest level wins: a main read listed before the search must still be read."""
        u = "https://x.org/p"
        lvl = self.synth(fetches=[{"role": "main", "url": u, "ok": True, "code": 200}],
                         searches=[{"role": "main", "query": "q", "links": [{"url": u}], "summary_chars": 5}])
        self.assertEqual(lvl, {arms.norm(u): "read"})

    def test_full_ladder_pairwise(self):
        order = ["sub_seen", "sub_read", "listed", "exposed", "read"]
        u = "https://www.ladder.org/a/"
        make = {
            "sub_seen": lambda: {"searches": [{"role": SEARCH_AGENT, "query": "q", "links": [{"url": u}],
                                               "summary_chars": 9}]},
            "sub_read": lambda: {"fetches": [{"role": "general-purpose", "url": u, "ok": True, "code": 200}]},
            "listed": lambda: {"searches": [{"role": "main", "query": "q", "links": [{"url": u}],
                                             "summary_chars": 0}]},
            "exposed": lambda: {"searches": [{"role": "main", "query": "q", "links": [{"url": u}],
                                              "summary_chars": 9}]},
            "read": lambda: {"fetches": [{"role": "main", "url": u, "ok": True, "code": 200}]},
        }
        for i, lo in enumerate(order):
            for hi in order[i:]:
                with self.subTest(lo=lo, hi=hi):
                    parts = {}
                    for name in (hi, lo):
                        for k, v in make[name]().items():
                            parts.setdefault(k, []).extend(v)
                    self.assertEqual(self.synth(**parts), {arms.norm(u): hi})

    def test_failed_fetches_add_nothing(self):
        lvl = self.synth(fetches=[{"role": "main", "url": "https://a.org", "ok": False, "code": 500},
                                  {"role": SEARCH_AGENT, "url": "https://b.org", "ok": False, "code": None}])
        self.assertEqual(lvl, {})



# --------------------------------------------------------------------------- B arms: cost

class TestCostBreakdown(WithFixture):

    def cost(self):
        return arms.cost_breakdown(self.ev)

    def test_total_is_sum_of_model_usage_cost(self):
        self.assertAlmostEqual(self.cost()["total_usd"], 0.0552 + 0.0120 + 0.0060, places=6)

    def test_by_role(self):
        r = self.cost()["by_role_usd"]
        self.assertEqual(set(r), {"main", "subagent"})
        self.assertAlmostEqual(r["main"], 0.0342, places=6)
        self.assertAlmostEqual(r["subagent"], 0.0015 + 0.0060, places=6)

    def test_web_search(self):
        c = self.cost()
        self.assertEqual(c["web_search_requests"], 3)
        self.assertAlmostEqual(c["web_search_usd"], 0.03, places=6)

    def test_tool_internal_per_model(self):
        t = self.cost()["tool_internal_usd"]
        self.assertAlmostEqual(t["claude-opus-5-5"], 0.0010, places=6)
        self.assertAlmostEqual(t["claude-haiku-5-5"], 0.0005, places=6)
        self.assertAlmostEqual(t["claude-sonnet-5-5"], 0.0, places=6)


# --------------------------------------------------------------------------- B arms: ingest

class RunsDir(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="arms-runs-")
        self._saved = arms.RUNS
        arms.RUNS = type(arms.RUNS)(os.path.join(self.tmp, "runs"))

    def tearDown(self):
        arms.RUNS = self._saved
        shutil.rmtree(self.tmp, ignore_errors=True)


BASE_SESSION = "sess-without-0002"
B_URLS = {"X": "https://xray.org/base-read", "Y": "https://yankee.org/base-exposed",
          "Z": "https://www.zulu.org/judged-already/"}


def without_trace_lines():
    links = [{"title": "T " + k, "url": B_URLS[k]} for k in ("X", "Y", "Z")]
    return [
        {"type": "system", "subtype": "init", "session_id": BASE_SESSION},
        a_msg("b1", None, [tool_use("bws", "WebSearch", {"query": "base q"})]),
        result_msg(None, "bws", "Links: " + json.dumps(links) + "\n\nsummary"),
        a_msg("b2", None, [tool_use("bwf", "WebFetch", {"url": B_URLS["X"]})]),
        result_msg(None, "bwf", "x text", tur={"code": 200, "bytes": 6}),
        {"type": "result", "result": "base report", "total_cost_usd": 0.01,
         "modelUsage": {"claude-opus-5-5": {"costUSD": 0.01, "webSearchRequests": 1}}},
    ]


class TestIngest(RunsDir):

    def setUp(self):
        super().setUp()
        os.makedirs(str(arms.RUNS))
        w = build_run(os.path.join(self.tmp, "sbx-with"), with_trace_lines(), SESSION, MAIN_SESSION,
                      {"A1": SEARCH_SESSION, "A2": OTHER_SESSION})
        o = build_run(os.path.join(self.tmp, "sbx-without"), without_trace_lines(), BASE_SESSION,
                      [a_msg("b1", None, [], usage={"input_tokens": 10, "output_tokens": 1})])
        self.trace_with, self.trace_without = w, o
        # blind judge has labelled Z (in another spelling) and A
        with open(os.path.join(str(arms.RUNS), "zz1.judge.json"), "w") as fh:
            json.dump({"id": "zz1", "judged": [{"url": "https://zulu.org/judged-already", "judgment": "useful"},
                                               {"url": "https://alpha.org/read-and-exposed", "judgment": "junk"}]}, fh)
        result = {"cases": [{"name": "zz1", "arms": {
            "with": [{"tracePath": w, "costUsd": 0.07, "turns": 12, "durationSeconds": 30,
                      "error": None, "startedAt": "2026-10-24T00:00:00Z"}],
            "without": [{"tracePath": o, "costUsd": 0.01, "turns": 3, "durationSeconds": 10,
                         "error": None, "startedAt": "2026-10-24T00:01:00Z"}]}}]}
        self.result_path = os.path.join(self.tmp, "result.json")
        with open(self.result_path, "w") as fh:
            json.dump(result, fh)
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            arms.cmd_ingest(self.result_path)

    def runs(self, *p):
        return os.path.join(str(arms.RUNS), *p)

    def test_trace_copies(self):
        for arm, src in (("with", self.trace_with), ("without", self.trace_without)):
            with self.subTest(arm=arm):
                with open(self.runs("zz1.%s.trace.jsonl" % arm), "rb") as a, open(src, "rb") as b:
                    self.assertEqual(a.read(), b.read())

    def test_events_written(self):
        for arm in ("with", "without"):
            with self.subTest(arm=arm):
                with open(self.runs("zz1.%s.events.json" % arm)) as fh:
                    ev = json.load(fh)
                for key in ("searches", "fetches", "skill_fired", "report_chars", "usage_by_role",
                            "main_peak_context"):
                    self.assertIn(key, ev)
        with open(self.runs("zz1.with.events.json")) as fh:
            ev = json.load(fh)
        self.assertIs(ev["skill_fired"], True)

    def test_session_copies(self):
        with_dir = self.runs("zz1.with.session")
        self.assertTrue(os.path.isdir(with_dir))
        self.assertEqual(sorted(os.listdir(with_dir)),
                         sorted([SESSION + ".jsonl", "agent-A1.jsonl", "agent-A2.jsonl"]))
        self.assertEqual(os.listdir(self.runs("zz1.without.session")), [BASE_SESSION + ".jsonl"])


    def test_union_todo(self):
        with open(self.runs("zz1.union.todo.json")) as fh:
            todo = json.load(fh)
        for item in todo:
            self.assertEqual(sorted(item), ["title", "url"])
        # Only URLs that reached the main context (listed/exposed/read) need a label: D (sub_seen) and
        # J (sub_read) drop out; P, seen by a subagent but also exposed in main, stays.
        want = ({arms.norm(U[k]) for k in MAIN_CONTEXT_KEYS} | {arms.norm(u) for u in B_URLS.values()}) \
            - {arms.norm(B_URLS["Z"]), arms.norm(U["A"])}
        self.assertEqual({arms.norm(i["url"]) for i in todo}, want)
        for k in ("D", "J"):
            self.assertNotIn(arms.norm(U[k]), {arms.norm(i["url"]) for i in todo}, k)
        self.assertIn(arms.norm(U["P"]), want)
        self.assertEqual(len(todo), len(want), "no duplicates")
        titles = {arms.norm(i["url"]): i["title"] for i in todo}
        self.assertEqual(titles[arms.norm(U["B"])], "T B")
        self.assertEqual(titles[arms.norm(B_URLS["Y"])], "T Y")

    def test_union_todo_without_judge_file_lists_everything(self):
        os.remove(self.runs("zz1.judge.json"))
        for f in os.listdir(self.runs()):
            if f.endswith(".events.json"):
                os.remove(self.runs(f))
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            arms.cmd_ingest(self.result_path)
        with open(self.runs("zz1.union.todo.json")) as fh:
            todo = json.load(fh)
        # "Everything" is every main-context URL of either arm; subagent-only D and J are still left out.
        want = {arms.norm(U[k]) for k in MAIN_CONTEXT_KEYS} | {arms.norm(u) for u in B_URLS.values()}
        self.assertEqual({arms.norm(i["url"]) for i in todo}, want)


# --------------------------------------------------------------------------- B arms: analyze

class TestAnalyzeArm(WithFixture):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        n = arms.norm
        cls.labels = {n(U["A"]): {"judgment": "junk"}, n(U["M"]): {"judgment": "useful"},
                      n(U["B"]): {"judgment": "marginal"}, n(U["J"]): {"judgment": "useful"},
                      n("https://never-reached.org/u"): {"judgment": "useful"}}
        cls.scores = {n(U["A"]): {"tier": "1"}, n(U["M"]): {"tier": "3"}, n(U["C"]): {"tier": "5"},
                      n(U["J"]): {"tier": "2"}}
        cls.out = arms.analyze_arm(cls.ev, cls.labels, cls.scores, {"id": "zz1", "mode": "academic", "field": "ai"})

    def test_contamination_read(self):
        c = self.out["contamination"]["read"]
        self.assertEqual(c["junk"].get("1"), 1)        # A
        self.assertEqual(c["useful"].get("3"), 1)      # M
        self.assertEqual(c["unlabeled"].get("?"), 1)   # O
        self.assertEqual(sum(sum(d.values()) for d in c.values()), 3)

    def test_contamination_exposed(self):
        c = self.out["contamination"]["exposed"]
        self.assertEqual(c["marginal"].get("?"), 1)    # B
        self.assertEqual(c["unlabeled"].get("?"), 1)   # P
        self.assertEqual(sum(sum(d.values()) for d in c.values()), 2)

    def test_contamination_listed(self):
        c = self.out["contamination"]["listed"]
        self.assertEqual(c["unlabeled"].get("5"), 1)   # C
        self.assertEqual(c["unlabeled"].get("?"), 1)   # L
        self.assertEqual(sum(sum(d.values()) for d in c.values()), 2)

    def test_contamination_sub_read(self):
        c = self.out["contamination"]["sub_read"]
        self.assertEqual(c["useful"].get("2"), 1)      # J
        self.assertEqual(sum(sum(d.values()) for d in c.values()), 1)

    def test_sources_read(self):
        s = self.out["sources_read"]
        self.assertEqual(s["main_attempted"], 6)       # A K L M N O
        self.assertEqual(s["main_ok"], 3)              # A M O
        self.assertEqual(s["main_by_label"], {"junk": 1, "useful": 1, "unlabeled": 1})
        self.assertEqual(s["sub_attempted"], 1)
        self.assertEqual(s["sub_ok"], 1)


    def test_useful_reached(self):
        self.assertEqual(set(self.out["useful_reached"]),
                         {arms.norm(U["M"]), arms.norm(U["J"])})


# --------------------------------------------------------------------------- C make_cases

MAKE_CASES = os.path.join(POLICY_EVAL, "make_cases.py")
QUESTIONS = os.path.join(POLICY_EVAL, "questions.json")
PILOT_ORDINALS = (1, 2, 3, 6)


class TestPilotQuestions(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        import make_cases
        cls.qs = list(make_cases.pilot_questions())
        with open(QUESTIONS) as fh:
            cls.doc = json.load(fh)

    def test_twenty_four_per_mode(self):
        self.assertEqual(len(self.qs), 20)
        by_mode = {}
        for q in self.qs:
            by_mode.setdefault(q["mode"], []).append(q)
        self.assertEqual(sorted(by_mode), sorted(self.doc["modes"]))
        for mode, qs in by_mode.items():
            with self.subTest(mode=mode):
                self.assertEqual(len(qs), 4)
                self.assertEqual(sum(1 for q in qs if q["expansion"]), 1)

    def test_ordinals_1_2_3_6(self):
        want = []
        for mode, block in self.doc["modes"].items():
            for n in PILOT_ORDINALS:
                want.append(block["questions"][n - 1]["id"])
        self.assertEqual(sorted(q["id"] for q in self.qs), sorted(want))

    def test_list_flag(self):
        proc = subprocess.run([sys.executable, MAKE_CASES, "--list"], capture_output=True, text=True)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        items = json.loads(proc.stdout)
        # the 20 pilot questions plus od9 (make_cases.EXTRA_IDS)
        self.assertEqual(len(items), 21)
        self.assertEqual(items[-1]["id"], "od9")
        for it in items:
            with self.subTest(id=it.get("id")):
                self.assertEqual(sorted(it), ["expansion", "field", "id", "mode", "q"])
                self.assertIsInstance(it["expansion"], bool)


class TestCaseYaml(unittest.TestCase):
    """Runs a byte copy of make_cases.py next to a copy of questions.json in a temp dir."""

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp(prefix="make-cases-")
        pe = os.path.join(cls.tmp, "policy-eval")
        os.makedirs(pe)
        shutil.copyfile(MAKE_CASES, os.path.join(pe, "make_cases.py"))
        shutil.copyfile(QUESTIONS, os.path.join(pe, "questions.json"))
        cls.proc = subprocess.run([sys.executable, "make_cases.py"], cwd=pe, capture_output=True, text=True)
        cls.arms_dir = os.path.join(pe, "arms")
        with open(QUESTIONS) as fh:
            doc = json.load(fh)
        cls.questions = {q["id"]: (mode, q) for mode, b in doc["modes"].items() for q in b["questions"]}

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def read(self, cid):
        with open(os.path.join(self.arms_dir, cid, "case.yaml")) as fh:
            return fh.read()

    def test_twenty_case_dirs(self):
        self.assertEqual(self.proc.returncode, 0, self.proc.stderr)
        self.assertEqual(len(os.listdir(self.arms_dir)), 21)

    def test_header_fields(self):
        for cid in os.listdir(self.arms_dir):
            with self.subTest(case=cid):
                y = self.read(cid)
                lines = [l.strip() for l in y.splitlines()]
                self.assertIn('schema_version: "1.1"', lines)
                self.assertIn("name: %s" % cid, lines)
                self.assertIn("runs: 1", lines)
                self.assertIn("model: claude-sonnet-5-5", lines)
                self.assertNotIn("claude-opus-5-5", y)
                tools_line = next(l for l in lines if l.startswith("allowed_tools:"))
                self.assertIn("WebSearch", tools_line)
                self.assertIn("WebFetch", tools_line)

    def test_prompt_contains_question_mode_field(self):
        for cid in os.listdir(self.arms_dir):
            mode, q = self.questions[cid]
            with self.subTest(case=cid):
                y = self.read(cid)
                prompt = y.split("prompt:", 1)[1].split("graders:", 1)[0]
                self.assertIn(q["q"], prompt)
                self.assertIn(mode, prompt)
                self.assertIn(q["field"], prompt)

    def test_two_named_graders_one_skill_with_only(self):
        for cid in os.listdir(self.arms_dir):
            with self.subTest(case=cid):
                graders = self.read(cid).split("graders:", 1)[1]
                items = [g for g in graders.split("\n  - ")[1:]]
                self.assertEqual(len(items), 2, graders)
                for g in items:
                    self.assertRegex(g, r"^name: \S+")
                skill = [g for g in items if "type: tool_used" in g]
                self.assertEqual(len(skill), 1)
                self.assertRegex(skill[0], r"\btool: Skill\b")
                self.assertRegex(skill[0], r"\barm: with-only\b")


# --------------------------------------------------------------------------- D contrast

U_NIST = "https://www.nist.gov/publications/synthetic-fixture-a"
U_RAND = "https://www.rand.org/pubs/research_reports/RRA0001.html"


class TestContrastPrefersUrlsJson(unittest.TestCase):
    """Temp mirror of the harness, as in test_expansion_eval.ContrastHarness.

    The two URL files differ on purpose: whichever one contrast.py scores decides n_scored.
    """

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp(prefix="contrast-urls-")
        os.makedirs(os.path.join(cls.tmp, "skills", "scored-web-search"))
        os.symlink(SCRIPTS, os.path.join(cls.tmp, "skills", "scored-web-search", "scripts"))
        cls.pe = os.path.join(cls.tmp, "policy-eval")
        cls.runs = os.path.join(cls.pe, "runs")
        os.makedirs(cls.runs)
        shutil.copyfile(os.path.join(POLICY_EVAL, "contrast.py"), os.path.join(cls.pe, "contrast.py"))

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def run_case(self, run_id, json_urls=None, txt_urls=None):
        judged = [{"url": u, "rank": i + 1, "title": "f", "judgment": "useful", "vendor_interest": False,
                   "has_primary_data": False, "basis": "snippet", "why": "f"}
                  for i, u in enumerate((U_NIST, U_RAND))]
        with open(os.path.join(self.runs, run_id + ".judge.json"), "w") as fh:
            json.dump({"id": run_id, "question": "q", "judged": judged}, fh)
        if json_urls is not None:
            with open(os.path.join(self.runs, run_id + ".urls.json"), "w") as fh:
                json.dump([{"url": u, "title": "f"} for u in json_urls], fh)
        if txt_urls is not None:
            with open(os.path.join(self.runs, run_id + ".urls.txt"), "w") as fh:
                fh.write("\n".join(txt_urls) + "\n")
        proc = subprocess.run([sys.executable, "contrast.py", run_id, "--mode", "academic", "--field", "ai"],
                              cwd=self.pe, capture_output=True, text=True)
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        with open(os.path.join(self.runs, run_id + ".contrast.json")) as fh:
            return json.load(fh)

    def test_json_preferred_when_both_exist(self):
        r = self.run_case("zzboth_a", json_urls=[U_NIST, U_RAND], txt_urls=[U_NIST])
        self.assertEqual((r["n_scored"], r["n_unscored"]), (2, 0))

    def test_json_preferred_when_both_exist_reverse(self):
        r = self.run_case("zzboth_b", json_urls=[U_NIST], txt_urls=[U_NIST, U_RAND])
        self.assertEqual((r["n_scored"], r["n_unscored"]), (1, 1))
        self.assertEqual(r["unscored"], [U_RAND])

    def test_json_only(self):
        r = self.run_case("zzjson", json_urls=[U_NIST, U_RAND])
        self.assertEqual(r["n_scored"], 2)

    def test_txt_only_still_works(self):
        r = self.run_case("zztxt", txt_urls=[U_NIST])
        self.assertEqual((r["n_scored"], r["n_unscored"]), (1, 1))


if __name__ == "__main__":
    unittest.main()
