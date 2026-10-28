"""Independent tests for the skill follow-ups, written from the specification (task brief), not from the
implementation.

Under test:

A. srcscore failed lookups by host and reason: `fetchers.http_json` return values and STATS counting,
   `fetchers.FAILURES` (Counter keyed by (host, reason): http-<code>, timeout, cut-off, unreachable, bad-json),
   and the `srcscore: failed lookups: ...` stderr line from `cli.main` (exit 0 and exit 4 cases).
B. srcscore one row per paper: `identifiers.paper_key`, `scoring.merge_same_paper` (grouping, kept row,
   tie-breaks, aliases/merged, order, no mutation), and `cli.main` (merge before --top, json keys, compact
   footer `merged k`), all with --no-net.
C. policy-eval/arms.py: agent_returns' `task` / `prompt_urls`, closing_lines' judge_skipped / verify_skipped /
   4-part unjudged, degraded_traced (rounds, coverage, judge_skipped, judge_unavailable, unjudged with
   missing, verify_skipped), degraded_check's agreement, analyze_arm passing the case mode, fetch_aliases
   joining merged copies, and cmd_ingest's per-paper todo.
D. skills/scored-web-search/SKILL.md: Step 2 merged rows, Step 2.5 judge contract, Step 5 Verify contract,
   Step 6 `Degraded:` item order.

Nothing here touches the network: in-process runs patch `urllib.request.urlopen` (and CLI merge tests use
--no-net with an urlopen that fails the test if called). arms.score_all and arms.case_list are stubbed and
arms.RUNS points at a temp dir.
"""

import contextlib
import copy
import http.client
import io
import json
import os
import re
import shutil
import socket
import sys
import tempfile
import unittest
import urllib.error
import urllib.parse
from collections import Counter
from unittest import mock

import _pathsetup  # noqa: F401
from srcscore_core import cli, fetchers, identifiers, scoring

ROOT = _pathsetup.ROOT
POLICY_EVAL = os.path.join(ROOT, "policy-eval")
if POLICY_EVAL not in sys.path:
    sys.path.insert(0, POLICY_EVAL)

import arms  # noqa: E402

SKILL_MD = os.path.join(ROOT, "skills", "scored-web-search", "SKILL.md")
n = arms.norm


# =========================================================================== A. fake network

class CutOff:
    """A reply whose headers arrive but whose body read raises `exc`."""

    def __init__(self, exc):
        self.exc = exc


class Raw:
    """A reply with a raw body and status."""

    def __init__(self, body, status=200):
        self.body = body
        self.status = status


class _Resp:
    def __init__(self, body=b"", status=200, read_exc=None):
        self.status = status
        self._body = body
        self._read_exc = read_exc

    def read(self, *a, **kw):
        if self._read_exc is not None:
            raise self._read_exc
        return self._body

    def getcode(self):
        return self.status

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def _http_error(url, code):
    return urllib.error.HTTPError(url, code, "status %d" % code, {}, None)


class FakeNet:
    """Stands in for urllib.request.urlopen. `rule(url, attempt)` returns a JSON-able payload, a Raw, a CutOff,
    or an exception to raise from urlopen itself."""

    def __init__(self, rule):
        self.rule = rule
        self.calls = []

    def __call__(self, req, timeout=None, *a, **kw):
        url = req.full_url if hasattr(req, "full_url") else str(req)
        k = self.calls.count(url)
        self.calls.append(url)
        out = self.rule(url, k)
        if isinstance(out, CutOff):
            return _Resp(read_exc=out.exc)
        if isinstance(out, Raw):
            return _Resp(out.body, out.status)
        if isinstance(out, BaseException):
            raise out
        return _Resp(json.dumps(out).encode("utf-8"))


def no_net(url, k):
    raise AssertionError("network touched: %s" % url)


@contextlib.contextmanager
def saved_state():
    s_stats = dict(fetchers.STATS)
    s_fail = Counter(fetchers.FAILURES)
    try:
        yield
    finally:
        fetchers.STATS.clear()
        fetchers.STATS.update(s_stats)
        fetchers.FAILURES.clear()
        fetchers.FAILURES.update(s_fail)


def call_http_json(rule, url):
    """One http_json call on a fake network with STATS and FAILURES zeroed.
    Returns (result, stats, failures, fake)."""
    fake = FakeNet(rule)
    with saved_state():
        fetchers.STATS["lookups"] = 0
        fetchers.STATS["failed"] = 0
        fetchers.FAILURES.clear()
        with mock.patch("urllib.request.urlopen", fake):
            result = fetchers.http_json(url, 1)
        stats = dict(fetchers.STATS)
        failures = Counter(fetchers.FAILURES)
    return result, stats, failures, fake


def run_cli(args, rule, preset_failures=None):
    """cli.main in-process with a fake network and an empty cache.
    Returns (exit code, stdout, stderr, stats, failures-after, fake)."""
    fake = FakeNet(rule)
    out, err = io.StringIO(), io.StringIO()
    with saved_state(), tempfile.TemporaryDirectory() as cache_dir, \
            mock.patch("urllib.request.urlopen", fake), \
            mock.patch.object(cli, "CACHE_DIR", cache_dir), \
            contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        fetchers.STATS["lookups"] = 0
        fetchers.STATS["failed"] = 0
        fetchers.FAILURES.clear()
        if preset_failures:
            fetchers.FAILURES.update(preset_failures)
        code = cli.main(list(args) + ["--workers", "1"])
        stats = dict(fetchers.STATS)
        failures = Counter(fetchers.FAILURES)
    return code, out.getvalue(), err.getvalue(), stats, failures, fake


OA_RECORD = {"id": "https://openalex.org/W1", "display_name": "A paper", "cited_by_count": 10,
             "publication_year": 2020, "publication_date": "2020-01-01", "is_retracted": False,
             "primary_location": {"source": {"display_name": "J", "type": "journal"}},
             "locations": [{"source": {"display_name": "J", "type": "journal"}}]}


def handle_record(target):
    return {"responseCode": 1, "handle": "10.1000/x",
            "values": [{"index": 1, "type": "URL", "data": {"format": "string", "value": target}}]}


def doi_url(suffix):
    return "https://doi.org/10.1000/" + suffix


def net_rule(oa_fail=None, handle_fail=None):
    """OpenAlex answers OA_RECORD unless a key of `oa_fail` (marker -> failure) is in the URL; the doi.org handle
    API resolves to a publisher page unless a key of `handle_fail` is in the URL. Other hosts: 404."""
    oa_fail = oa_fail or {}
    handle_fail = handle_fail or {}

    def pick(table, url):
        for marker, failure in table.items():
            if marker in url:
                return failure() if callable(failure) else failure
        return None

    def rule(url, k):
        host = urllib.parse.urlsplit(url).hostname
        if host == "api.openalex.org":
            return pick(oa_fail, url) or OA_RECORD
        if host == "doi.org":
            return pick(handle_fail, url) or handle_record("https://publisher.example/article/1")
        if host == "hn.algolia.com":
            return {"hits": []}
        return _http_error(url, 404)
    return rule


def args_for(*suffixes, fmt="json"):
    return sum((["-u", doi_url(s)] for s in suffixes), []) + ["--format", fmt]


URL = "https://api.openalex.org/works/W1"
HOST = "api.openalex.org"


# =========================================================================== A1. http_json + FAILURES

class TestHttpJsonFailures(unittest.TestCase):

    def check(self, rule, want_result, want_reason, url=URL, host=HOST):
        result, stats, failures, fake = call_http_json(rule, url)
        if want_result is fetchers.FETCH_FAILED:
            self.assertIs(result, fetchers.FETCH_FAILED)
            self.assertEqual(stats, {"lookups": 1, "failed": 1})
            self.assertEqual(failures, Counter({(host, want_reason): 1}))
        else:
            self.assertEqual(result, want_result)
            self.assertEqual(stats, {"lookups": 1, "failed": 0})
            self.assertEqual(failures, Counter(), "a non-failed lookup adds nothing to FAILURES")
        return fake

    def test_failures_is_a_counter(self):
        self.assertIsInstance(fetchers.FAILURES, Counter)

    def test_success_is_json_and_no_failure(self):
        self.check(lambda u, k: {"ok": 1}, {"ok": 1}, None)

    def test_404_and_410_are_none_not_failures(self):
        for code in (404, 410):
            with self.subTest(code=code):
                self.check(lambda u, k, code=code: _http_error(u, code), None, None)

    def test_non_200_success_status_is_none_not_failure(self):
        for status in (201, 204):
            with self.subTest(status=status):
                self.check(lambda u, k, s=status: Raw(b"", s), None, None)

    def test_http_status_reasons(self):
        for code in (401, 403, 407, 429, 500, 502, 503):
            with self.subTest(code=code):
                self.check(lambda u, k, code=code: _http_error(u, code), fetchers.FETCH_FAILED, "http-%d" % code)

    def test_timeout_reasons(self):
        cases = {
            "TimeoutError on open": lambda u, k: TimeoutError("timed out"),
            "socket.timeout on open": lambda u, k: socket.timeout("timed out"),
            "URLError(TimeoutError)": lambda u, k: urllib.error.URLError(TimeoutError("timed out")),
            "URLError(socket.timeout)": lambda u, k: urllib.error.URLError(socket.timeout("timed out")),
            "TimeoutError reading body": lambda u, k: CutOff(TimeoutError("timed out")),
        }
        for name, rule in cases.items():
            with self.subTest(case=name):
                self.check(rule, fetchers.FETCH_FAILED, "timeout")

    def test_cut_off_twice(self):
        cases = {
            "IncompleteRead": lambda: http.client.IncompleteRead(b'{"a', 10),
            "RemoteDisconnected": lambda: http.client.RemoteDisconnected("closed"),
            "HTTPException": lambda: http.client.HTTPException("cut"),
            "ConnectionResetError": lambda: ConnectionResetError(54, "reset"),
        }
        for name, make in cases.items():
            with self.subTest(case=name):
                fake = self.check(lambda u, k, make=make: CutOff(make()), fetchers.FETCH_FAILED, "cut-off")
                self.assertEqual(len(fake.calls), 2)

    def test_cut_off_once_then_ok_adds_nothing(self):
        fake = self.check(lambda u, k: CutOff(ConnectionResetError("reset")) if k == 0 else {"ok": 2},
                          {"ok": 2}, None)
        self.assertEqual(len(fake.calls), 2)

    def test_unreachable_reasons(self):
        cases = {
            "URLError DNS": lambda u, k: urllib.error.URLError(socket.gaierror(8, "nodename nor servname")),
            "URLError text": lambda u, k: urllib.error.URLError("unreachable"),
            "URLError(ConnectionRefusedError)": lambda u, k: urllib.error.URLError(ConnectionRefusedError(61, "x")),
            "URLError(ConnectionResetError) on connect": lambda u, k: urllib.error.URLError(ConnectionResetError("r")),
            "ConnectionRefusedError": lambda u, k: ConnectionRefusedError(61, "refused"),
            "OSError": lambda u, k: OSError(65, "no route to host"),
        }
        for name, rule in cases.items():
            with self.subTest(case=name):
                self.check(rule, fetchers.FETCH_FAILED, "unreachable")

    def test_bad_json(self):
        for body in (b"<html>not json</html>", b"", b'{"truncated": '):
            with self.subTest(body=body):
                self.check(lambda u, k, b=body: Raw(b, 200), fetchers.FETCH_FAILED, "bad-json")

    def test_host_is_lowercased(self):
        self.check(lambda u, k: _http_error(u, 503), fetchers.FETCH_FAILED, "http-503",
                   url="https://API.OpenAlex.ORG/works/W1", host="api.openalex.org")

    def test_host_is_netloc_with_port(self):
        self.check(lambda u, k: _http_error(u, 429), fetchers.FETCH_FAILED, "http-429",
                   url="http://LocalHost:8080/x", host="localhost:8080")

    def test_failures_accumulate_per_call(self):
        fake = FakeNet(lambda u, k: _http_error(u, 429) if "a" in urllib.parse.urlsplit(u).path
                       else urllib.error.URLError("x"))
        with saved_state():
            fetchers.STATS["lookups"] = 0
            fetchers.STATS["failed"] = 0
            fetchers.FAILURES.clear()
            with mock.patch("urllib.request.urlopen", fake):
                for p in ("/a1", "/a2", "/b", "/a3"):
                    fetchers.http_json("https://api.github.com" + p, 1)
                fetchers.http_json("https://hn.algolia.com/b", 1)
            stats = dict(fetchers.STATS)
            failures = Counter(fetchers.FAILURES)
        self.assertEqual(stats, {"lookups": 5, "failed": 5})
        self.assertEqual(failures, Counter({("api.github.com", "http-429"): 3, ("api.github.com", "unreachable"): 1,
                                           ("hn.algolia.com", "unreachable"): 1}))


# =========================================================================== A2. cli stderr line

LOOKUPS_LINE = re.compile(r"^srcscore: (\d+) of (\d+) external lookups could not be reached", re.M)


def failed_lookups_line(err):
    lines = [l for l in err.splitlines() if l.startswith("srcscore: failed lookups: ")]
    return lines


class TestCliFailedLookupsLine(unittest.TestCase):

    def test_partial_failure_exit0(self):
        code, out, err, stats, _, _ = run_cli(
            args_for("ok1", "ok2", "fail1"), net_rule(oa_fail={"fail": urllib.error.URLError("x")}))
        self.assertEqual(stats, {"lookups": 6, "failed": 1})
        self.assertEqual(code, 0, err)
        m = LOOKUPS_LINE.search(err)
        self.assertTrue(m, err)
        self.assertEqual((m.group(1), m.group(2)), ("1", "6"))
        self.assertEqual(failed_lookups_line(err), ["srcscore: failed lookups: api.openalex.org unreachable x1"])

    def test_sorted_by_count_desc(self):
        code, out, err, stats, _, _ = run_cli(
            args_for("ok1", "ok2", "ok3", "oafail1", "oafail2", "hfail1"),
            net_rule(oa_fail={"oafail": lambda: _http_error("x", 429)},
                     handle_fail={"hfail": lambda: urllib.error.URLError(TimeoutError("t"))}))
        self.assertEqual(stats, {"lookups": 12, "failed": 3})
        self.assertEqual(code, 0, err)
        self.assertEqual(failed_lookups_line(err),
                         ["srcscore: failed lookups: api.openalex.org http-429 x2, doi.org timeout x1"])

    def test_ties_by_host_then_reason(self):
        # doi.org fails x1 and api.openalex.org fails x1 per reason: equal counts sort by host, then reason.
        code, out, err, stats, _, _ = run_cli(
            args_for("ok1", "ok2", "ok3", "ok4", "ok5", "aa503", "aa429", "hh503"),
            net_rule(oa_fail={"aa503": lambda: _http_error("x", 503), "aa429": lambda: _http_error("x", 429)},
                     handle_fail={"hh503": lambda: _http_error("x", 503)}))
        self.assertEqual(stats, {"lookups": 16, "failed": 3})
        self.assertEqual(code, 0, err)
        self.assertEqual(failed_lookups_line(err), ["srcscore: failed lookups: api.openalex.org http-429 x1, "
                                                    "api.openalex.org http-503 x1, doi.org http-503 x1"])

    def test_mostly_failed_exit4(self):
        code, out, err, stats, _, _ = run_cli(
            args_for("ok1", "fail1", "fail2"),
            net_rule(oa_fail={"fail": lambda: _http_error("x", 503)},
                     handle_fail={"fail": lambda: _http_error("x", 503)}))
        self.assertEqual(stats, {"lookups": 6, "failed": 4})
        self.assertEqual(code, 4, err)
        m = LOOKUPS_LINE.search(err)
        self.assertTrue(m, err)
        self.assertEqual((m.group(1), m.group(2)), ("4", "6"))
        self.assertEqual(failed_lookups_line(err),
                         ["srcscore: failed lookups: api.openalex.org http-503 x2, doi.org http-503 x2"])

    def test_all_failed_mixed_reasons_exit4(self):
        code, out, err, stats, _, _ = run_cli(
            args_for("a", "b"),
            net_rule(oa_fail={"10.1000": lambda: urllib.error.URLError("dns")},
                     handle_fail={"10.1000": lambda: Raw(b"<html>", 200)}))
        self.assertEqual(code, 4, err)
        self.assertEqual(failed_lookups_line(err),
                         ["srcscore: failed lookups: api.openalex.org unreachable x2, doi.org bad-json x2"])

    def test_no_failure_no_line(self):
        code, out, err, stats, _, _ = run_cli(args_for("ok1", "ok2"), net_rule())
        self.assertEqual(code, 0, err)
        self.assertEqual(stats["failed"], 0)
        self.assertNotIn("failed lookups", err)
        self.assertNotIn("could not be reached", err)

    def test_failures_cleared_before_scoring(self):
        stale = Counter({("stale.example", "http-500"): 7})
        code, out, err, stats, failures, _ = run_cli(args_for("ok1", "ok2"), net_rule(), preset_failures=stale)
        self.assertEqual(code, 0, err)
        self.assertNotIn("failed lookups", err)
        self.assertNotIn(("stale.example", "http-500"), failures)
        code, out, err, stats, failures, _ = run_cli(
            args_for("ok1", "ok2", "fail1"), net_rule(oa_fail={"fail": urllib.error.URLError("x")}),
            preset_failures=stale)
        self.assertNotIn("stale.example", err)
        self.assertEqual(failed_lookups_line(err), ["srcscore: failed lookups: api.openalex.org unreachable x1"])


# =========================================================================== B1. paper_key

class TestPaperKey(unittest.TestCase):

    def eq(self, url, key):
        with self.subTest(url=url):
            self.assertEqual(identifiers.paper_key(url), key)

    def test_arxiv_new_style(self):
        for u in ("https://arxiv.org/abs/2401.01234", "https://arxiv.org/abs/2401.01234v2",
                  "https://arxiv.org/pdf/2401.01234", "https://arxiv.org/pdf/2401.01234v1",
                  "https://arxiv.org/html/2401.01234v3", "https://arxiv.org/html/2401.01234",
                  "http://www.arxiv.org/abs/2401.01234v12"):
            self.eq(u, "arxiv:2401.01234")

    def test_arxiv_old_style(self):
        for u in ("https://arxiv.org/abs/hep-th/9901001", "https://arxiv.org/abs/hep-th/9901001v2",
                  "https://arxiv.org/pdf/hep-th/9901001v1"):
            self.eq(u, "arxiv:hep-th/9901001")

    def test_arxiv_old_style_with_subject_class_lowercased(self):
        self.eq("https://arxiv.org/abs/math.GT/0309136v1", "arxiv:math.gt/0309136")

    def test_arxiv_doi_maps_to_arxiv(self):
        for u in ("https://doi.org/10.48550/arXiv.2401.01234", "https://doi.org/10.48550/ARXIV.2401.01234",
                  "https://doi.org/10.48550/arxiv.2401.01234"):
            self.eq(u, "arxiv:2401.01234")

    def test_other_doi_lowercased(self):
        self.eq("https://doi.org/10.1038/Nature12345", "doi:10.1038/nature12345")
        self.eq("https://doi.org/10.1007/S00123-020-01234-5", "doi:10.1007/s00123-020-01234-5")

    def test_doi_link_and_publisher_page_share_key(self):
        a = identifiers.paper_key("https://doi.org/10.1007/s00123-020-01234-5")
        b = identifiers.paper_key("https://link.springer.com/article/10.1007/s00123-020-01234-5")
        self.assertEqual(a, "doi:10.1007/s00123-020-01234-5")
        self.assertEqual(a, b)
        self.eq("https://dl.acm.org/doi/10.1145/3292500.3330701", "doi:10.1145/3292500.3330701")

    def test_pubmed(self):
        self.eq("https://pubmed.ncbi.nlm.nih.gov/12345678/", "pmid:12345678")
        self.eq("https://pubmed.ncbi.nlm.nih.gov/12345678", "pmid:12345678")

    def test_none(self):
        for u in ("https://www.semanticscholar.org/paper/Attention-Is-All-You-Need/204e3073870fae3d05bcbc2f6a8e263d9b72e776",
                  "https://blog.example.com/2024/01/my-post", "https://github.com/o/r",
                  "https://arxiv.org/list/cs.AI/recent", "https://news.ycombinator.com/item?id=1"):
            self.eq(u, None)


# =========================================================================== B2. merge_same_paper

def mk(url, score, fetch_url=None, tag=None):
    return {"url": url, "fetch_url": fetch_url or url, "score": score, "verdict": "SUPPORT", "flags": [],
            "tag": tag if tag is not None else url}


ABS = "https://arxiv.org/abs/2401.01234"
PDF = "https://arxiv.org/pdf/2401.01234v1"
HTML = "https://arxiv.org/html/2401.01234v2"
ADOI = "https://doi.org/10.48550/arXiv.2401.01234"
BLOG = "https://blog.example.com/post"
BLOG2 = "https://blog.example.com/other"
SPR = "https://link.springer.com/article/10.1007/s00123-020-01234-5"
SDOI = "https://doi.org/10.1007/s00123-020-01234-5"


class TestMergeSamePaper(unittest.TestCase):

    def merge(self, rows):
        before = copy.deepcopy(rows)
        out = scoring.merge_same_paper(rows)
        self.assertEqual(rows, before, "input rows must not be mutated")
        return list(out)

    def test_nothing_to_merge(self):
        rows = [mk(BLOG, 50), mk(ABS, 80), mk(BLOG2, 60)]
        out = self.merge(rows)
        self.assertEqual(out, rows)
        for r in out:
            self.assertNotIn("aliases", r)
            self.assertNotIn("merged", r)

    def test_highest_score_kept(self):
        out = self.merge([mk(PDF, 70), mk(ABS, 90), mk(HTML, 80)])
        self.assertEqual(len(out), 1)
        r = out[0]
        self.assertEqual(r["url"], ABS)
        self.assertEqual(r["score"], 90)
        self.assertEqual(r["aliases"], [PDF, HTML])
        self.assertEqual(r["merged"], 2)

    def test_tie_html_beats_abs_beats_pdf(self):
        out = self.merge([mk(PDF, 70), mk(ABS, 70), mk(HTML, 70)])
        self.assertEqual([r["url"] for r in out], [HTML])
        self.assertEqual(out[0]["aliases"], [PDF, ABS])
        out = self.merge([mk(PDF, 70), mk(ABS, 70)])
        self.assertEqual([r["url"] for r in out], [ABS])
        self.assertEqual(out[0]["aliases"], [PDF])

    def test_tie_pdf_beats_non_arxiv(self):
        out = self.merge([mk(ADOI, 70), mk(PDF, 70)])
        self.assertEqual([r["url"] for r in out], [PDF])
        self.assertEqual(out[0]["aliases"], [ADOI])
        self.assertEqual(out[0]["merged"], 1)

    def test_tie_break_reads_the_fetch_address(self):
        # Both arXiv: the pdf URL whose fetch address is /html/ beats an abs URL fetched as /abs/.
        a = mk(ABS, 70, fetch_url=ABS, tag="abs")
        b = mk(PDF, 70, fetch_url="https://arxiv.org/html/2401.01234v1", tag="pdf-as-html")
        out = self.merge([a, b])
        self.assertEqual(len(out), 1)
        self.assertEqual(out[0]["tag"], "pdf-as-html")

    def test_score_beats_readability(self):
        out = self.merge([mk(HTML, 60), mk(PDF, 61)])
        self.assertEqual([r["url"] for r in out], [PDF])

    def test_further_tie_earliest_input(self):
        out = self.merge([mk(SPR, 70, tag="first"), mk(SDOI, 70, tag="second")])
        self.assertEqual(len(out), 1)
        self.assertEqual(out[0]["tag"], "first")
        self.assertEqual(out[0]["aliases"], [SDOI])
        a = mk(ABS, 70, tag="a1")
        b = mk("https://arxiv.org/abs/2401.01234v3", 70, tag="a2")
        out = self.merge([a, b])
        self.assertEqual(out[0]["tag"], "a1")

    def test_identical_url_three_times(self):
        out = self.merge([mk(BLOG, 50, tag=1), mk(BLOG, 50, tag=2), mk(BLOG, 50, tag=3)])
        self.assertEqual(len(out), 1)
        self.assertEqual(out[0]["tag"], 1)
        self.assertEqual(out[0]["merged"], 2)
        self.assertEqual(out[0]["aliases"], [])

    def test_identical_url_repeats_inside_a_paper_group(self):
        out = self.merge([mk(PDF, 50), mk(ABS, 90), mk(PDF, 50), mk(ABS, 90, tag="abs-again")])
        self.assertEqual(len(out), 1)
        r = out[0]
        self.assertEqual(r["url"], ABS)
        self.assertEqual(r["tag"], ABS, "earliest of the tied best rows")
        self.assertEqual(r["aliases"], [PDF], "deduplicated, without the kept row's own URL")
        self.assertEqual(r["merged"], 3)

    def test_no_key_urls_group_only_by_exact_url(self):
        out = self.merge([mk(BLOG, 50), mk(BLOG2, 50), mk(BLOG + "/", 50)])
        self.assertEqual(len(out), 3)
        for r in out:
            self.assertNotIn("merged", r)

    def test_doi_link_and_publisher_page_merge(self):
        out = self.merge([mk(SDOI, 60), mk(SPR, 75)])
        self.assertEqual([r["url"] for r in out], [SPR])
        self.assertEqual(out[0]["aliases"], [SDOI])
        self.assertEqual(out[0]["merged"], 1)

    def test_arxiv_doi_joins_arxiv_group(self):
        out = self.merge([mk(ADOI, 85), mk(ABS, 70)])
        self.assertEqual([r["url"] for r in out], [ADOI])
        self.assertEqual(out[0]["aliases"], [ABS])

    def test_old_style_arxiv_group(self):
        out = self.merge([mk("https://arxiv.org/pdf/hep-th/9901001v1", 60),
                          mk("https://arxiv.org/abs/hep-th/9901001v2", 60)])
        self.assertEqual([r["url"] for r in out], ["https://arxiv.org/abs/hep-th/9901001v2"])

    def test_output_order_by_kept_row_position(self):
        rows = [mk(PDF, 50), mk(BLOG, 60), mk(SDOI, 40), mk(ABS, 90), mk(BLOG2, 10), mk(SPR, 80)]
        out = self.merge(rows)
        self.assertEqual([r["url"] for r in out], [BLOG, ABS, BLOG2, SPR])
        self.assertNotIn("merged", out[0])
        self.assertEqual(out[1]["aliases"], [PDF])
        self.assertEqual(out[3]["aliases"], [SDOI])

    def test_kept_row_keeps_its_fields(self):
        src = mk(ABS, 90, tag="kept")
        src["title"] = "T"
        out = self.merge([mk(PDF, 50), src])
        r = out[0]
        for k, v in src.items():
            self.assertEqual(r[k], v, k)

    def test_aliases_in_input_order_deduplicated(self):
        out = self.merge([mk(HTML, 50), mk(ADOI, 40), mk(HTML, 50), mk(ABS, 99), mk(PDF, 30), mk(ADOI, 40)])
        self.assertEqual(out[0]["url"], ABS)
        self.assertEqual(out[0]["aliases"], [HTML, ADOI, PDF])
        self.assertEqual(out[0]["merged"], 5)

    def test_empty(self):
        self.assertEqual(self.merge([]), [])


# =========================================================================== B3. cli merge (--no-net)

FOOTER_RE = re.compile(r"^-- (\d+) sources \| PRIMARY (\d+) \| SUPPORT (\d+) \| SKIM (\d+) \| WEAK (\d+) \| "
                       r"DROP (\d+) \| BLOCKED (\d+)( \| merged (\d+))?$", re.M)

C_ABS = "https://arxiv.org/abs/2401.01234"
C_PDF = "https://arxiv.org/pdf/2401.01234"
C_HTML = "https://arxiv.org/html/2401.01234v1"


def urls_args(*urls, fmt="json", extra=()):
    return sum((["-u", u] for u in urls), []) + ["--format", fmt, "--no-net"] + list(extra)


class TestCliMerge(unittest.TestCase):

    def run_ok(self, args):
        code, out, err, stats, failures, fake = run_cli(args, no_net)
        self.assertEqual(code, 0, err)
        self.assertEqual(fake.calls, [])
        return out

    def test_arxiv_copies_collapse_json(self):
        out = self.run_ok(urls_args(C_ABS, C_PDF, C_HTML, BLOG, BLOG, BLOG2))
        rows = json.loads(out)
        self.assertEqual(len(rows), 3)
        by_key = {}
        for r in rows:
            by_key.setdefault(identifiers.paper_key(r["url"]) or r["url"], []).append(r)
        self.assertEqual(len(by_key["arxiv:2401.01234"]), 1)
        a = by_key["arxiv:2401.01234"][0]
        self.assertEqual(a["merged"], 2)
        self.assertEqual(len(a["aliases"]), 2)
        self.assertEqual(set(a["aliases"]) | {a["url"]}, {C_ABS, C_PDF, C_HTML})
        b = by_key[BLOG][0]
        self.assertEqual((b["merged"], b["aliases"]), (1, []))
        c = by_key[BLOG2][0]
        self.assertNotIn("merged", c)
        self.assertNotIn("aliases", c)

    def test_compact_footer_counts_after_merge(self):
        out = self.run_ok(urls_args(C_ABS, C_PDF, C_HTML, BLOG, BLOG, fmt="compact"))
        m = FOOTER_RE.search(out)
        self.assertTrue(m, out)
        self.assertEqual(int(m.group(1)), 2)
        self.assertEqual(sum(int(m.group(i)) for i in range(2, 8)), 2)
        self.assertEqual(m.group(9), "3")
        self.assertTrue(m.group(0).endswith(" | BLOCKED %s | merged 3" % m.group(7)), m.group(0))

    def test_compact_footer_without_merges(self):
        out = self.run_ok(urls_args(C_ABS, BLOG, fmt="compact"))
        m = FOOTER_RE.search(out)
        self.assertTrue(m, out)
        self.assertEqual(int(m.group(1)), 2)
        self.assertIsNone(m.group(8))
        self.assertNotIn("merged", out)

    def test_merge_happens_before_top(self):
        out = self.run_ok(urls_args(C_ABS, C_PDF, C_HTML, BLOG, BLOG2, extra=["--top", "2"]))
        rows = json.loads(out)
        self.assertEqual(len(rows), 2)
        keys = [identifiers.paper_key(r["url"]) or r["url"] for r in rows]
        self.assertEqual(len(set(keys)), 2)

    def test_merge_happens_before_min(self):
        # The merged row's best copy decides whether the paper passes --min; no copy of it survives twice.
        out = self.run_ok(urls_args(C_ABS, C_PDF, C_HTML, extra=["--min", "0"]))
        rows = json.loads(out)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["merged"], 2)

    def test_old_style_and_doi_collapse(self):
        out = self.run_ok(urls_args("https://arxiv.org/abs/hep-th/9901001v1", "https://arxiv.org/pdf/hep-th/9901001",
                                    SDOI, SPR))
        rows = json.loads(out)
        self.assertEqual(len(rows), 2)
        self.assertEqual(sorted(r["merged"] for r in rows), [1, 1])


# =========================================================================== C. arms trace builder

SEARCHER = "scored-web-search:searcher"
JUDGE = "general-purpose"
VERDICTS = ("PRIMARY", "SUPPORT", "SKIM", "WEAK", "DROP", "BLOCKED")
SCORER_CMD = "python3 /x/skills/scored-web-search/scripts/srcscore.py --in %s --mode academic --format compact"
FRAME_HEAD = ("[Subagent hand-back] The text below is the final report of a subagent this session delegated to. "
              "The harness indents every line of the report. The report follows:\n")


def handback(reply, aid):
    body = "\n".join("  " + l for l in reply.split("\n"))
    return FRAME_HEAD + body + "\nagentId: %s\n<usage>subagent_tokens: 100\ntool_uses: 2\nduration_ms: 900</usage>" % aid


def a_msg(mid, parent, content):
    return {"type": "assistant", "parent_tool_use_id": parent,
            "message": {"id": mid, "model": "claude-opus-5-5", "content": content,
                        "usage": {"input_tokens": 1, "output_tokens": 1}}}


def result_msg(parent, tid, content, tur=None, is_error=None):
    block = {"type": "tool_result", "tool_use_id": tid, "content": content}
    if is_error is not None:
        block["is_error"] = is_error
    d = {"type": "user", "parent_tool_use_id": parent, "message": {"content": [block]}}
    if tur is not None:
        d["tool_use_result"] = tur
    return d


def trow(score, verdict, url):
    return "%5.1f %-8s %-2s %-27s %s" % (score, verdict, "2", "", url)


def table(*pairs):
    """A compact table from (verdict, url) pairs."""
    counts = Counter(v for v, _ in pairs)
    lines = ["SCORE VERDICT T  SIGNALS                     URL  TITLE"]
    lines += [trow(90.0 if v == "PRIMARY" else 65.0, v, u) for v, u in pairs]
    lines.append("-- %d sources | %s" % (len(pairs), " | ".join("%s %d" % (v, counts.get(v, 0)) for v in VERDICTS)))
    return "\n".join(lines)


def judge_prompt(urls, fmt="USE/SKIP/UNJUDGED"):
    return ("Research question: q?\nRead each page with WebFetch. Return only one line per URL: "
            "`url | %s | reason`.\n\n%s" % (fmt, "\n".join(urls)))


def verify_prompt(urls, fmt="supported/secondary/contradicted"):
    return ("For each figure or claim: (a) ... Answer only in the format `claim | %s | location of evidence`.\n%s"
            % (fmt, "\n".join(urls)))


def verdicts(*items):
    return "\n".join("%s | %s | %s" % it for it in items)


class Trace:
    def __init__(self):
        self.L = [{"type": "system", "subtype": "init", "session_id": "sess-followups-1"}]
        self.k = 0

    def _id(self, p):
        self.k += 1
        return "%s%d" % (p, self.k)

    def call(self, name, inp, result, parent=None, is_error=None, tur=None):
        tid = self._id("tu")
        self.L.append(a_msg(self._id("m"), parent, [{"type": "tool_use", "id": tid, "name": name, "input": inp}]))
        self.L.append(result_msg(parent, tid, result, tur=tur, is_error=is_error))
        return tid

    def score(self, out, parent=None, is_error=None, path="/tmp/ssw/urls-1.json"):
        return self.call("Bash", {"command": SCORER_CMD % path}, out, parent, is_error)

    def fetch(self, url, parent=None, code=200, text="page text"):
        return self.call("WebFetch", {"url": url, "prompt": "p"}, text, parent, tur={"code": code, "bytes": len(text)})

    def agent(self, subtype, reply=None, prompt="go", is_error=False, body=None):
        tid = self._id("ag")
        self.L.append(a_msg(self._id("m"), None, [{"type": "tool_use", "id": tid, "name": "Agent", "input": {
            "subagent_type": subtype, "prompt": prompt, "description": "d"}}]))
        self.L.append({"type": "system", "subtype": "task_started", "tool_use_id": tid,
                       "subagent_type": subtype, "is_backgrounded": False, "description": "d"})
        if body:
            body(tid)
        if reply is not None:
            self.L.append(a_msg(self._id("m"), tid, [{"type": "text", "text": reply}]))
        aid = "agent" + tid
        if is_error:
            self.L.append(result_msg(None, tid, "Agent failed: API error", is_error=True))
        else:
            self.L.append(result_msg(None, tid, [{"type": "text", "text": handback(reply or "", aid)}],
                                     tur={"agentId": aid, "agentType": subtype, "status": "completed"}))
        return tid

    def report(self, text):
        self.L.append({"type": "result", "subtype": "success", "result": text, "num_turns": 9,
                       "total_cost_usd": 0.01,
                       "modelUsage": {"claude-opus-5-5": {"costUSD": 0.01, "webSearchRequests": 0}}})

    def dump(self, path):
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w") as fh:
            for d in self.L:
                fh.write(json.dumps(d) + "\n")
        return path


class TraceCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="arms-followups-")
        self.i = 0

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def extract(self, t, report="Report.\nDegraded: none"):
        if not any(d.get("type") == "result" for d in t.L):
            t.report(report)
        self.i += 1
        return arms.extract(t.dump(os.path.join(self.tmp, "r%d" % self.i, "trace", "trace.jsonl")))


A = "https://s.org/a"
B = "https://s.org/b"
C = "https://s.org/c"
D = "https://s.org/d"
P = "https://p.org/1"


# =========================================================================== C1. agent_returns task/prompt_urls

class TestAgentReturnTask(TraceCase):

    def ret(self, **kw):
        t = Trace()
        t.agent(JUDGE, **kw)
        rets = self.extract(t)["agent_returns"]
        self.assertEqual(len(rets), 1)
        return rets[0]

    def test_judge_task_and_sorted_unique_prompt_urls(self):
        r = self.ret(reply=verdicts((A, "USE", "ok")), prompt=judge_prompt([C, A, B, A]))
        self.assertEqual(r["task"], "judge")
        self.assertEqual(r["prompt_urls"], [A, B, C])

    def test_judge_format_whitespace_variants(self):
        for fmt in ("USE/SKIP/UNJUDGED", "USE / SKIP / UNJUDGED", "USE  /SKIP/  UNJUDGED"):
            with self.subTest(fmt=fmt):
                r = self.ret(reply="nothing", prompt=judge_prompt([A], fmt=fmt))
                self.assertEqual(r["task"], "judge")
                self.assertEqual(r["prompt_urls"], [A])

    def test_verify_task(self):
        for fmt in ("supported/secondary/contradicted", "supported / secondary / contradicted"):
            with self.subTest(fmt=fmt):
                r = self.ret(reply="claim | supported | p.2", prompt=verify_prompt([P, A], fmt=fmt))
                self.assertEqual(r["task"], "verify")
                self.assertEqual(r["prompt_urls"], [])

    def test_other_task(self):
        r = self.ret(reply="/tmp/ssw/urls-1.json 3",
                     prompt="Search for q and write https://x.org/a style records to /tmp/ssw/urls-1.json")
        self.assertIsNone(r["task"])
        self.assertEqual(r["prompt_urls"], [])

    def test_errored_judge_keeps_task(self):
        r = self.ret(reply=None, prompt=judge_prompt([B, A]), is_error=True)
        self.assertEqual(r["task"], "judge")
        self.assertEqual(r["prompt_urls"], [A, B])
        self.assertNotEqual(r["kind"], "judge")

    def test_every_return_has_the_keys(self):
        t = Trace()
        t.agent(SEARCHER, reply="/tmp/ssw/urls-1.json 2", prompt="search")
        t.agent(JUDGE, reply=verdicts((A, "USE", "ok")), prompt=judge_prompt([A]))
        t.agent(JUDGE, reply="claim | supported | x", prompt=verify_prompt([A]))
        rets = self.extract(t)["agent_returns"]
        self.assertEqual([r["task"] for r in rets], [None, "judge", "verify"])
        self.assertEqual([r["prompt_urls"] for r in rets], [[], [A], []])


# =========================================================================== C2. closing_lines

class TestClosingLines(unittest.TestCase):

    def d(self, line):
        return arms.closing_lines("Body.\n3 sources collected → 1 passed → 0 judged-out → 1 read\nDegraded: "
                                  + line + "\nKeywords: r1: q")["degraded"]

    def test_none(self):
        d = self.d("none")
        self.assertIs(d["none"], True)
        for k in ("judge_unavailable", "judge_skipped", "verify_skipped"):
            self.assertIs(d[k], False, k)

    def test_flags(self):
        d = self.d("judge-skipped · verify-skipped")
        self.assertEqual((d["judge_skipped"], d["verify_skipped"], d["judge_unavailable"]), (True, True, False))
        d = self.d("judge-unavailable")
        self.assertEqual((d["judge_skipped"], d["verify_skipped"], d["judge_unavailable"]), (False, False, True))
        d = self.d("lookups-failed 1/9 · judge-unavailable · judge-skipped")
        self.assertEqual((d["judge_skipped"], d["judge_unavailable"]), (True, True))

    def test_unjudged_four_parts(self):
        for item, want in (("unjudged 6 (fetch-error 1, page 2, missing 3)", [6, 1, 2, 3]),
                           ("unjudged 6 (missing 3, fetch-error 1, page 2)", [6, 1, 2, 3]),
                           ("unjudged 4 (missing 3, page 1)", [4, 0, 1, 3]),
                           ("unjudged 2 (missing 2)", [2, 0, 0, 2]),
                           ("unjudged 1", [1, 0, 0, 0]),
                           ("unjudged 2 (missing 2) · unjudged 3 (fetch-error 1, missing 2)", [5, 1, 0, 4]),
                           ("judge-skipped · unjudged 3 (page 1, missing 2) · verify-skipped", [3, 0, 1, 2])):
            with self.subTest(item=item):
                self.assertEqual(self.d(item)["unjudged"], want)

    def test_full_line(self):
        d = self.d("step1-inline 1/3 · lookups-failed 2/40 · judge-unavailable · judge-skipped · "
                   "unjudged 4 (fetch-error 1, page 1, missing 2) · verify-skipped")
        self.assertEqual(d["step1_inline"], [1, 3])
        self.assertEqual(d["lookups_failed"], [2, 40])
        self.assertEqual(d["unjudged"], [4, 1, 1, 2])
        self.assertIs(d["judge_unavailable"], True)
        self.assertIs(d["judge_skipped"], True)
        self.assertIs(d["verify_skipped"], True)


# =========================================================================== C3. degraded_traced

KEYS = {"step1_inline", "lookups_failed", "judge_unavailable", "judge_skipped", "unjudged", "verify_skipped"}


class TestDegradedTracedFollowups(TraceCase):

    def traced(self, t, mode=None):
        d = arms.degraded_traced(self.extract(t), mode)
        self.assertEqual(set(d), KEYS)
        return d

    def test_judge_retry_answers_missing(self):
        t = Trace()
        t.score(table(("SUPPORT", A), ("SUPPORT", B), ("SUPPORT", C)))
        t.agent(JUDGE, verdicts((A, "USE", "ok"), (B, "SKIP", "off")), prompt=judge_prompt([A, B, C]))
        t.agent(JUDGE, verdicts((C, "USE", "ok")), prompt=judge_prompt([C]))
        d = self.traced(t)
        self.assertEqual(d["unjudged"], [0, 0, 0, 0])
        self.assertIs(d["judge_skipped"], False)
        self.assertIs(d["judge_unavailable"], False)

    def test_missing_without_retry(self):
        t = Trace()
        t.score(table(("SUPPORT", A), ("SUPPORT", B), ("SUPPORT", C)))
        t.agent(JUDGE, verdicts((A, "USE", "ok"), (B, "SKIP", "off")), prompt=judge_prompt([A, B, C]))
        d = self.traced(t)
        self.assertEqual(d["unjudged"], [1, 0, 0, 1])
        self.assertIs(d["judge_skipped"], False)
        self.assertIs(d["judge_unavailable"], False)

    def test_retry_gives_unjudged_page(self):
        t = Trace()
        t.score(table(("SUPPORT", A), ("SUPPORT", B)))
        t.agent(JUDGE, verdicts((A, "USE", "ok")), prompt=judge_prompt([A, B]))
        t.agent(JUDGE, verdicts((B, "UNJUDGED", "page: login wall")), prompt=judge_prompt([B]))
        self.assertEqual(self.traced(t)["unjudged"], [1, 0, 1, 0])

    def test_unjudged_reasons_counted(self):
        t = Trace()
        t.score(table(("PRIMARY", P), ("SUPPORT", A), ("SUPPORT", B), ("SUPPORT", C), ("SUPPORT", D)))
        t.agent(JUDGE, verdicts((A, "USE", "ok"), (B, "UNJUDGED", "page: empty"),
                                (C, "UNJUDGED", "fetch-error: twice"), (D, "UNJUDGED", "paywalled")),
                prompt=judge_prompt([A, B, C, D]))
        d = self.traced(t)
        self.assertEqual(d["unjudged"], [3, 1, 1, 0])
        self.assertIs(d["judge_unavailable"], False)

    def test_errored_judge_identified_by_prompt(self):
        t = Trace()
        t.score(table(("SUPPORT", A)))
        t.agent(JUDGE, None, prompt=judge_prompt([A]), is_error=True)
        d = self.traced(t)
        self.assertIs(d["judge_unavailable"], True)
        self.assertIs(d["judge_skipped"], False, "the errored attempt's prompt covers the round")
        self.assertEqual(d["unjudged"], [0, 0, 0, 0])

    def test_judge_task_without_verdict_lines_is_unavailable(self):
        t = Trace()
        t.score(table(("SUPPORT", A)))
        t.agent(JUDGE, "Sorry, I could not do this.", prompt=judge_prompt([A]))
        d = self.traced(t)
        self.assertIs(d["judge_unavailable"], True)
        self.assertIs(d["judge_skipped"], False)

    def test_errored_judge_on_other_urls_does_not_cover(self):
        t = Trace()
        t.score(table(("SUPPORT", A)))
        t.agent(JUDGE, None, prompt=judge_prompt([D]), is_error=True)
        d = self.traced(t)
        self.assertIs(d["judge_unavailable"], True)
        self.assertIs(d["judge_skipped"], True)

    def test_failed_then_working_attempt(self):
        t = Trace()
        t.score(table(("SUPPORT", A), ("SUPPORT", B)))
        t.agent(JUDGE, verdicts((A, "UNJUDGED", "fetch-error: x"), (B, "UNJUDGED", "fetch-error: y")),
                prompt=judge_prompt([A, B]))
        t.agent(JUDGE, verdicts((A, "USE", "ok"), (B, "UNJUDGED", "page: shell")), prompt=judge_prompt([A, B]))
        d = self.traced(t)
        self.assertIs(d["judge_unavailable"], True)
        self.assertIs(d["judge_skipped"], False)
        self.assertEqual(d["unjudged"], [1, 0, 1, 0], "the all-fetch-error attempt's verdicts are not counted")

    def test_prompt_alone_covers_round(self):
        # A working attempt whose prompt names the round's URL, but whose verdicts are all on another URL.
        t = Trace()
        t.score(table(("SUPPORT", A)))
        t.agent(JUDGE, verdicts((D, "USE", "ok")), prompt=judge_prompt([A, D]))
        d = self.traced(t)
        self.assertIs(d["judge_skipped"], False)
        self.assertIs(d["judge_unavailable"], False)
        self.assertEqual(d["unjudged"], [1, 0, 0, 1])

    def test_verdicts_alone_cover_round(self):
        t = Trace()
        t.score(table(("SUPPORT", A)))
        t.agent(JUDGE, verdicts((A, "SKIP", "off")), prompt="judge these")
        d = self.traced(t)
        self.assertIs(d["judge_skipped"], False)
        self.assertEqual(d["unjudged"], [0, 0, 0, 0])

    def test_coverage_via_norm(self):
        t = Trace()
        t.score(table(("SUPPORT", "https://example.org/a")))
        t.agent(JUDGE, verdicts(("https://www.example.org/a/", "USE", "ok")),
                prompt=judge_prompt(["https://www.example.org/a/"]))
        d = self.traced(t)
        self.assertIs(d["judge_skipped"], False)
        self.assertEqual(d["unjudged"], [0, 0, 0, 0])

    def test_second_round_skipped_missing_only_from_covered_rounds(self):
        t = Trace()
        t.score(table(("SUPPORT", A), ("SUPPORT", B)))
        t.agent(JUDGE, verdicts((A, "USE", "ok")), prompt=judge_prompt([A, B]))
        t.score(table(("SUPPORT", A), ("SUPPORT", C), ("SUPPORT", D)))
        d = self.traced(t)
        self.assertIs(d["judge_skipped"], True)
        self.assertIs(d["judge_unavailable"], False)
        self.assertEqual(d["unjudged"], [1, 0, 0, 1], "C and D belong to an uncovered round")

    def test_rescoring_seen_urls_is_no_round(self):
        t = Trace()
        t.score(table(("SUPPORT", A)))
        t.agent(JUDGE, verdicts((A, "USE", "ok")), prompt=judge_prompt([A]))
        t.score(table(("SUPPORT", A), ("PRIMARY", P)))
        d = self.traced(t)
        self.assertIs(d["judge_skipped"], False)

    def test_primary_only_needs_no_judge(self):
        t = Trace()
        t.score(table(("PRIMARY", P)))
        d = self.traced(t)
        self.assertEqual((d["judge_skipped"], d["judge_unavailable"]), (False, False))

    def test_failed_score_run_is_no_round(self):
        t = Trace()
        t.score("Exit code 4\nsrcscore: 9 of 10 external lookups could not be reached", is_error=True)
        t.score(table(("PRIMARY", P)))
        d = self.traced(t)
        self.assertEqual((d["judge_skipped"], d["judge_unavailable"]), (False, False))

    def test_support_round_with_no_judge_at_all(self):
        t = Trace()
        t.score(table(("SUPPORT", A)))
        d = self.traced(t)
        self.assertIs(d["judge_skipped"], True)
        self.assertIs(d["judge_unavailable"], False)
        self.assertEqual(d["unjudged"], [0, 0, 0, 0])

    # ----- verify_skipped

    def read_trace(self, verify=None, fetch_code=200, sub_only=False):
        t = Trace()
        t.score(table(("PRIMARY", P)))
        if sub_only:
            t.agent(JUDGE, "claim | supported | p1", prompt="Read and summarise",
                    body=lambda tid: t.fetch(P, parent=tid))
        else:
            t.fetch(P, code=fetch_code)
        if verify == "ok":
            t.agent(JUDGE, "x | supported | p.1", prompt=verify_prompt([P]))
        elif verify == "error":
            t.agent(JUDGE, None, prompt=verify_prompt([P]), is_error=True)
        return t

    def test_verify_skipped_in_academic(self):
        self.assertIs(self.traced(self.read_trace(), "academic")["verify_skipped"], True)

    def test_verify_ran_in_academic(self):
        self.assertIs(self.traced(self.read_trace(verify="ok"), "academic")["verify_skipped"], False)

    def test_errored_verify_still_ran(self):
        self.assertIs(self.traced(self.read_trace(verify="error"), "academic")["verify_skipped"], False)

    def test_verify_not_required_outside_academic(self):
        for mode in (None, "news", "non-academic", "official-docs", "community-opinion"):
            with self.subTest(mode=mode):
                self.assertIs(self.traced(self.read_trace(), mode)["verify_skipped"], False)

    def test_verify_in_non_academic_mode(self):
        self.assertIs(self.traced(self.read_trace(verify="ok"), "news")["verify_skipped"], False)

    def test_no_successful_main_read(self):
        self.assertIs(self.traced(self.read_trace(fetch_code=404), "academic")["verify_skipped"], False)
        self.assertIs(self.traced(self.read_trace(sub_only=True), "academic")["verify_skipped"], False)


# =========================================================================== C4. degraded_check / analyze_arm

class TestDegradedCheckFollowups(TraceCase):

    def skipped_trace(self):
        t = Trace()
        t.score(table(("SUPPORT", A), ("PRIMARY", P)))
        t.fetch(P)
        return t

    def test_judge_skipped_agreement(self):
        ev = self.extract(self.skipped_trace(), "R\nDegraded: judge-skipped")
        self.assertIs(arms.degraded_check(ev)["agree"]["judge_skipped"], True)
        ev = self.extract(self.skipped_trace(), "R\nDegraded: none")
        self.assertIs(arms.degraded_check(ev)["agree"]["judge_skipped"], False)
        ev = self.extract(self.skipped_trace(), "R\nDegraded: judge-unavailable")
        a = arms.degraded_check(ev)["agree"]
        self.assertIs(a["judge_skipped"], False)
        self.assertIs(a["judge_unavailable"], False)

    def test_verify_skipped_agreement_uses_mode(self):
        ev = self.extract(self.skipped_trace(), "R\nDegraded: judge-skipped · verify-skipped")
        self.assertIs(arms.degraded_check(ev, "academic")["agree"]["verify_skipped"], True)
        self.assertIs(arms.degraded_check(ev, "news")["agree"]["verify_skipped"], False)
        ev = self.extract(self.skipped_trace(), "R\nDegraded: judge-skipped")
        self.assertIs(arms.degraded_check(ev, "academic")["agree"]["verify_skipped"], False)
        self.assertIs(arms.degraded_check(ev, "news")["agree"]["verify_skipped"], True)

    def test_missing_reported_keys_count_as_false(self):
        ev = self.extract(self.skipped_trace(), "R\nDegraded: none")
        ev["closing"]["degraded"] = {"raw": "x", "none": False}
        a = arms.degraded_check(ev, "news")["agree"]
        self.assertIs(a["judge_unavailable"], True)
        self.assertIs(a["verify_skipped"], True)
        self.assertIs(a["judge_skipped"], False)
        ev["closing"]["degraded"] = {"raw": "x", "none": False, "judge_skipped": True}
        self.assertIs(arms.degraded_check(ev, "news")["agree"]["judge_skipped"], True)

    def test_unjudged_compared_by_first_number(self):
        def build():
            t = Trace()
            t.score(table(("SUPPORT", A), ("SUPPORT", B), ("SUPPORT", C)))
            t.agent(JUDGE, verdicts((A, "USE", "ok"), (B, "UNJUDGED", "page: x")), prompt=judge_prompt([A, B, C]))
            return t
        ev = self.extract(build(), "R\nDegraded: unjudged 2 (page 1, missing 1)")
        self.assertEqual(arms.degraded_check(ev)["traced"]["unjudged"], [2, 0, 1, 1])
        self.assertIs(arms.degraded_check(ev)["agree"]["unjudged"], True)
        ev = self.extract(build(), "R\nDegraded: unjudged 2 (fetch-error 2)")
        self.assertIs(arms.degraded_check(ev)["agree"]["unjudged"], True, "first number only")
        ev = self.extract(build(), "R\nDegraded: unjudged 1 (page 1)")
        self.assertIs(arms.degraded_check(ev)["agree"]["unjudged"], False)

    def test_agree_keys(self):
        ev = self.extract(self.skipped_trace(), "R\nDegraded: none")
        self.assertEqual(set(arms.degraded_check(ev)["agree"]), KEYS)


class TestAnalyzeArmMode(TraceCase):

    def test_case_mode_passed(self):
        t = Trace()
        t.score(table(("PRIMARY", P)))
        t.fetch(P)
        ev = self.extract(t, "R\nDegraded: none")
        out = arms.analyze_arm(ev, {}, {}, {"id": "t1", "mode": "academic", "field": "ai"})
        self.assertIs(out["degraded"]["traced"]["verify_skipped"], True)
        self.assertIs(out["degraded"]["agree"]["verify_skipped"], False)
        out = arms.analyze_arm(ev, {}, {}, {"id": "t1", "mode": "news", "field": "ai"})
        self.assertIs(out["degraded"]["traced"]["verify_skipped"], False)
        self.assertIs(out["degraded"]["agree"]["verify_skipped"], True)


# =========================================================================== C5. fetch_aliases

class TestFetchAliasesMerged(unittest.TestCase):

    KEPT = "https://arxiv.org/abs/2401.01234v2"
    COPY1 = "https://arxiv.org/pdf/2401.01234v1"
    COPY2 = "https://doi.org/10.48550/arXiv.2401.01234"

    def scores(self):
        return {n(self.KEPT): {"url": self.KEPT, "fetch_url": self.KEPT, "score": 80.0, "verdict": "PRIMARY",
                               "aliases": [self.COPY1, self.COPY2], "merged": 2},
                n(BLOG): {"url": BLOG, "fetch_url": BLOG, "score": 60.0, "verdict": "SUPPORT"}}

    def test_aliases_map_to_kept_row(self):
        alias = arms.fetch_aliases(self.scores())
        self.assertEqual(alias.get(n(self.COPY1)), n(self.KEPT))
        self.assertEqual(alias.get(n(self.COPY2)), n(self.KEPT))
        self.assertNotIn(n(BLOG), alias)

    def test_fetch_target_still_mapped(self):
        sc = self.scores()
        sc[n(SDOI)] = {"url": SDOI, "fetch_url": SPR, "score": 70.0, "verdict": "SUPPORT"}
        alias = arms.fetch_aliases(sc)
        self.assertEqual(alias.get(n(SPR)), n(SDOI))
        self.assertEqual(alias.get(n(self.COPY1)), n(self.KEPT))

    def test_label_on_copy_joins_kept_row(self):
        alias = arms.fetch_aliases(self.scores())
        labels = arms.aliased_labels({n(self.COPY2): {"judgment": "useful"}}, alias)
        self.assertEqual(labels.get(n(self.KEPT)), {"judgment": "useful"})

    def test_read_of_copy_joins_kept_row(self):
        alias = arms.fetch_aliases(self.scores())
        ev = {"searches": [], "score_runs": [], "agent_returns": [],
              "fetches": [{"url": self.COPY2, "ok": True, "role": "main"}]}
        self.assertEqual(arms.arm_urls(ev, alias).get(n(self.KEPT)), "read")


# =========================================================================== C6. cmd_ingest todo per paper

class TestIngestPaperTodo(unittest.TestCase):

    CASE = {"id": "pk1", "mode": "academic", "field": "ai", "expansion": False, "q": "q?"}

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="arms-followups-ingest-")
        self.saved = (arms.RUNS, arms.case_list, arms.score_all)
        arms.RUNS = type(arms.RUNS)(os.path.join(self.tmp, "runs"))
        os.makedirs(str(arms.RUNS))
        self.score_calls = []
        arms.case_list = lambda: {self.CASE["id"]: dict(self.CASE)}
        arms.score_all = self.fake_score_all

    def tearDown(self):
        arms.RUNS, arms.case_list, arms.score_all = self.saved
        shutil.rmtree(self.tmp, ignore_errors=True)

    def fake_score_all(self, urls, mode, field):
        urls = list(urls)
        self.score_calls.append(urls)
        return {n(u): {"url": u, "fetch_url": u, "score": 70.0, "verdict": "SUPPORT", "flags": []} for u in urls}

    POOL = ["https://arxiv.org/abs/2401.01234v2", "https://doi.org/10.48550/arXiv.2401.01234",
            "https://arxiv.org/pdf/2401.01234v3", SPR, SDOI, "https://arxiv.org/abs/2402.00002",
            "https://www.semanticscholar.org/paper/aaa", "https://www.semanticscholar.org/paper/bbb", BLOG]

    def ingest(self, judged=None):
        t = Trace()
        t.score(table(*[("SUPPORT", u) for u in self.POOL]))
        t.report("R\nDegraded: none")
        tp = t.dump(os.path.join(self.tmp, "w", "trace", "trace.jsonl"))
        res = os.path.join(self.tmp, "result.json")
        with open(res, "w") as fh:
            json.dump({"cases": [{"name": "pk1", "arms": {"with": [{"tracePath": tp, "error": None}]}}]}, fh)
        if judged is not None:
            with open(os.path.join(str(arms.RUNS), "pk1.judge.json"), "w") as fh:
                json.dump({"judged": [{"url": u, "judgment": "useful"} for u in judged]}, fh)
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            arms.cmd_ingest(res)
        with open(os.path.join(str(arms.RUNS), "pk1.union.todo.json")) as fh:
            return [i["url"] for i in json.load(fh)]

    def keys(self, todo):
        return Counter(identifiers.paper_key(u) for u in todo)

    def test_one_url_per_paper_key(self):
        todo = self.ingest()
        k = self.keys(todo)
        self.assertEqual(k["arxiv:2401.01234"], 1)
        self.assertEqual(k["doi:10.1007/s00123-020-01234-5"], 1)
        self.assertEqual(k["arxiv:2402.00002"], 1)
        self.assertEqual(k[None], 3, "URLs without a paper key are never folded: two S2 pages and the blog")
        self.assertEqual(len(todo), 6)

    def test_labelled_paper_left_out(self):
        todo = self.ingest(judged=["https://arxiv.org/html/2401.01234v1"])
        k = self.keys(todo)
        self.assertNotIn("arxiv:2401.01234", k)
        self.assertEqual(k["doi:10.1007/s00123-020-01234-5"], 1)
        self.assertEqual(k["arxiv:2402.00002"], 1)
        self.assertEqual(k[None], 3)

    def test_labelled_doi_link_leaves_publisher_page_out(self):
        todo = self.ingest(judged=[SDOI])
        k = self.keys(todo)
        self.assertNotIn("doi:10.1007/s00123-020-01234-5", k)
        self.assertEqual(k["arxiv:2401.01234"], 1)

    def test_labelled_url_without_key_drops_only_itself(self):
        todo = self.ingest(judged=["https://www.semanticscholar.org/paper/aaa"])
        self.assertEqual(self.keys(todo)[None], 2)
        self.assertNotIn(n("https://www.semanticscholar.org/paper/aaa"), {n(u) for u in todo})


# =========================================================================== D. SKILL.md

def read(path):
    with open(path, encoding="utf-8") as f:
        return f.read()


def squash(text):
    return re.sub(r"\s+", " ", text)


def section(md, heading_re):
    m = re.search(r"^(#+)\s*" + heading_re + r".*$", md, re.M)
    if not m:
        raise AssertionError("no section matching %r" % heading_re)
    level = len(m.group(1))
    rest = md[m.end():]
    nxt = re.search(r"^#{1,%d}\s" % level, rest, re.M)
    return rest[: nxt.start()] if nxt else rest


class TestSkillMd(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.md = read(SKILL_MD)

    def test_step2_mentions_merge_and_footer(self):
        s = squash(section(self.md, r"Step 2 —"))
        self.assertRegex(s, r"(?i)one row")
        self.assertRegex(s, r"(?i)paper")
        self.assertIn("merged k", s)

    def test_step25_judge_required(self):
        s = squash(section(self.md, r"Step 2\.5"))
        self.assertRegex(s, r"(?i)whenever there are SUPPORT rows[^.]*required|required[^.]*whenever there are SUPPORT rows")

    def test_step25_judge_unavailable_only_cases(self):
        s = squash(section(self.md, r"Step 2\.5"))
        self.assertIn("judge-unavailable", s)
        self.assertRegex(s, r"(?i)no subagent can be launched")
        self.assertRegex(s, r"(?i)gave no verdicts")
        self.assertRegex(s, r"(?i)only uses of `judge-unavailable`")

    def test_step25_judge_skipped(self):
        s = squash(section(self.md, r"Step 2\.5"))
        self.assertRegex(s, r"(?i)`judge-skipped`")
        self.assertRegex(s, r"(?i)skipped it[^.]*`judge-skipped`")
        self.assertRegex(s, r"(?i)subagent can be launched[^.]*judge-skipped|judge-skipped[^.]*could be launched|"
                            r"subagent can be launched, never skip")

    def test_step25_missing_retry(self):
        s = squash(section(self.md, r"Step 2\.5"))
        self.assertRegex(s, r"(?i)left out[^.]*once more|once more[^.]*left out")
        self.assertRegex(s, r"UNJUDGED with the reason `missing:`")

    def test_step5_verify_contract(self):
        s = squash(section(self.md, r"Step 5"))
        self.assertRegex(s, r"(?i)required in `academic` mode")
        self.assertRegex(s, r"(?i)Step 3 read anything")
        self.assertIn("`verify-skipped`", s)
        self.assertRegex(s, r"(?i)only when no subagent can be launched")
        self.assertRegex(s, r"(?i)tracing it is required|tracing[^.]{0,40}required")

    def test_step6_degraded_items_in_order(self):
        s = section(self.md, r"Step 6")
        items = re.findall(r"^- `([^`]+)`", s, re.M)
        want = ["step1-inline <k>/<n>", "lookups-failed <f>/<t>", "judge-unavailable", "judge-skipped",
                "unjudged <u> (fetch-error <f>, page <p>, missing <m>)", "verify-skipped"]
        self.assertEqual(items, want)


if __name__ == "__main__":
    unittest.main()
