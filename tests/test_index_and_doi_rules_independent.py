"""Independent tests for two rules: doi.org links are read through doi.org's handle
API (`fetchers.doi_target`, the scorer's `fetch_url`, the eval's fetch aliases), and
the per-host non-content rule `non_content_host_path_regex` (Semantic Scholar paper
pages). Written from the spec.

Nothing here touches the network: in-process runs patch `urllib.request.urlopen`;
subprocess runs use `--no-net` only."""

import contextlib
import copy
import io
import json
import os
import socket
import subprocess
import sys
import tempfile
import unittest
import urllib.error
import urllib.parse
from unittest import mock

import _pathsetup  # noqa: F401
from srcscore_core import cli, fetchers
from srcscore_core.policy import PolicyError, load_policy, validate_policy

ROOT = _pathsetup.ROOT
SRCSCORE = os.path.join(_pathsetup.SCRIPTS, "srcscore.py")
POLICY_JSON = os.path.join(_pathsetup.SCRIPTS, "policy.json")
CHECK_POLICY = os.path.join(ROOT, "scripts", "check_policy.py")
POLICY_EVAL = os.path.join(ROOT, "policy-eval")
if POLICY_EVAL not in sys.path:
    sys.path.insert(0, POLICY_EVAL)

import arms  # noqa: E402


# --------------------------------------------------------------------------- fake network

class _Resp:
    def __init__(self, payload, status=200):
        self.status = status
        self._body = json.dumps(payload).encode("utf-8")

    def read(self, *a, **kw):
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def _http_error(url, code):
    return urllib.error.HTTPError(url, code, "status %d" % code, {}, None)


class FakeNet:
    """Stands in for urllib.request.urlopen. `rule(url)` returns a payload, or an
    exception instance to raise. Every requested URL is recorded."""

    def __init__(self, rule):
        self.rule = rule
        self.calls = []

    def __call__(self, req, timeout=None, *a, **kw):
        url = req.full_url if hasattr(req, "full_url") else str(req)
        self.calls.append(url)
        out = self.rule(url)
        if isinstance(out, BaseException):
            raise out
        return _Resp(out)

    def to(self, host):
        return [u for u in self.calls if urllib.parse.urlsplit(u).hostname == host]


@contextlib.contextmanager
def clean_stats():
    saved = dict(fetchers.STATS)
    fetchers.STATS["lookups"] = 0
    fetchers.STATS["failed"] = 0
    try:
        yield fetchers.STATS
    finally:
        fetchers.STATS.clear()
        fetchers.STATS.update(saved)


OA_RECORD = {"id": "https://openalex.org/W1", "display_name": "A paper", "cited_by_count": 10,
             "publication_year": 2020, "publication_date": "2020-01-01", "is_retracted": False,
             "primary_location": {"source": {"display_name": "J", "type": "journal"}},
             "locations": [{"source": {"display_name": "J", "type": "journal"}}]}


def value(typ, val, index=1):
    return {"index": index, "type": typ, "data": {"format": "string", "value": val}}


def handle(*values):
    return {"responseCode": 1, "handle": "10.1000/x", "values": list(values)}


def handle_url(doi):
    return "https://doi.org/api/handles/%s?type=URL" % doi


def doi_url(suffix, host="doi.org"):
    return "https://%s/10.1000/%s" % (host, suffix)


def net_rule(targets, handle_fail=None, oa_fail=None):
    """OpenAlex answers a record for every DOI. The handle API resolves the DOI suffix
    via `targets` ({suffix: target URL}; a suffix missing from it is a 404). A suffix in
    `handle_fail` / `oa_fail` gets that failure from that service instead."""
    handle_fail = handle_fail or {}
    oa_fail = oa_fail or {}

    def rule(url):
        p = urllib.parse.urlsplit(url)
        if p.hostname == "api.openalex.org":
            for sfx, f in oa_fail.items():
                if sfx in url:
                    return f
            return OA_RECORD
        if p.hostname == "doi.org":
            sfx = urllib.parse.unquote(p.path).rsplit("/", 1)[-1]
            if sfx in handle_fail:
                return handle_fail[sfx]
            if sfx in targets:
                return handle(value("HS_ADMIN", {"handle": "0.NA/10.1000"}, 100),
                              value("URL", targets[sfx]))
            return _http_error(url, 404)
        if p.hostname == "hn.algolia.com":
            return {"hits": []}
        return _http_error(url, 404)
    return rule


def run_cli(args, rule):
    """cli.main in-process with a fake network and an empty cache.
    Returns (exit code, stdout, stderr, stats, fake)."""
    fake = FakeNet(rule)
    out, err = io.StringIO(), io.StringIO()
    with tempfile.TemporaryDirectory() as cache_dir, clean_stats() as stats, \
            mock.patch("urllib.request.urlopen", fake), \
            mock.patch.object(cli, "CACHE_DIR", cache_dir), \
            contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        code = cli.main(list(args) + ["--workers", "1"])
        stats = dict(stats)
    return code, out.getvalue(), err.getvalue(), stats, fake


def json_rows(urls, rule, *extra):
    args = sum((["-u", u] for u in urls), []) + ["--format", "json"] + list(extra)
    code, out, err, stats, fake = run_cli(args, rule)
    if code != 0:
        raise AssertionError("CLI exit %d: %s" % (code, err))
    return {r["url"]: r for r in json.loads(out)}, err, stats, fake


# --------------------------------------------------------------------------- 1. doi_target

class DoiTarget(unittest.TestCase):
    def call(self, rule, doi="10.1000/abc", cache=None):
        fake = FakeNet(rule)
        with clean_stats() as stats, mock.patch("urllib.request.urlopen", fake):
            got = fetchers.doi_target(doi, cache or fetchers.NullCache(), 1)
            stats = dict(stats)
        return got, fake, stats

    def test_in_lookup_hosts(self):
        self.assertIn("doi.org", fetchers.LOOKUP_HOSTS)
        self.assertEqual(set(fetchers.LOOKUP_HOSTS),
                         {"api.openalex.org", "api.semanticscholar.org", "api.github.com",
                          "hn.algolia.com", "doi.org"})

    def test_asks_handle_api(self):
        _, fake, _ = self.call(lambda u: handle(value("URL", "https://p.example/a")))
        self.assertEqual(fake.calls, [handle_url("10.1000/abc")])

    def test_returns_url_value(self):
        got, _, _ = self.call(lambda u: handle(value("URL", "https://p.example/a")))
        self.assertEqual(got, "https://p.example/a")

    def test_http_url_accepted(self):
        got, _, _ = self.call(lambda u: handle(value("URL", "http://p.example/a")))
        self.assertEqual(got, "http://p.example/a")

    def test_first_http_url_entry_wins(self):
        got, _, _ = self.call(lambda u: handle(
            value("HS_ADMIN", "https://admin.example/", 100),
            value("EMAIL", "https://mail.example/", 2),
            value("URL", "ftp://files.example/a", 3),
            value("URL", "https://first.example/a", 4),
            value("URL", "https://second.example/a", 5)))
        self.assertEqual(got, "https://first.example/a")

    def test_no_url_value_is_none(self):
        for name, payload in (("only admin", handle(value("HS_ADMIN", "https://admin.example/", 100))),
                              ("non-http url", handle(value("URL", "mailto:a@b.example"))),
                              ("empty values", handle()),
                              ("no values key", {"responseCode": 1, "handle": "10.1000/abc"})):
            with self.subTest(name):
                got, _, _ = self.call(lambda u, p=payload: p)
                self.assertIsNone(got)

    def test_not_found_is_none(self):
        got, _, stats = self.call(lambda u: _http_error(u, 404))
        self.assertIsNone(got)
        self.assertEqual(stats, {"lookups": 1, "failed": 0})

    def test_unreachable_is_fetch_failed(self):
        failures = {"unreachable": urllib.error.URLError("x"), "refused": ConnectionRefusedError("x"),
                    "timeout": socket.timeout("x"), "401": 401, "403": 403, "407": 407, "429": 429,
                    "500": 500, "503": 503}
        for name, f in failures.items():
            with self.subTest(failure=name):
                got, _, stats = self.call(lambda u, f=f: _http_error(u, f) if isinstance(f, int) else f)
                self.assertIs(got, fetchers.FETCH_FAILED)
                self.assertEqual(stats, {"lookups": 1, "failed": 1})

    def test_found_result_cached(self):
        with tempfile.TemporaryDirectory() as d:
            cache = fetchers.Cache(d)
            first, f1, _ = self.call(lambda u: handle(value("URL", "https://p.example/a")), cache=cache)
            second, f2, _ = self.call(lambda u: AssertionError("must not be asked"), cache=cache)
        self.assertEqual(len(f1.calls), 1)
        self.assertEqual(f2.calls, [])
        self.assertEqual(second, first)
        self.assertEqual(second, "https://p.example/a")

    def test_not_found_result_cached(self):
        with tempfile.TemporaryDirectory() as d:
            cache = fetchers.Cache(d)
            first, f1, _ = self.call(lambda u: _http_error(u, 404), cache=cache)
            second, f2, _ = self.call(lambda u: AssertionError("must not be asked"), cache=cache)
        self.assertIsNone(first)
        self.assertIsNone(second)
        self.assertEqual(len(f1.calls), 1)
        self.assertEqual(f2.calls, [])

    def test_other_doi_not_served_from_cache(self):
        with tempfile.TemporaryDirectory() as d:
            cache = fetchers.Cache(d)
            self.call(lambda u: handle(value("URL", "https://p.example/a")), doi="10.1000/a", cache=cache)
            got, f2, _ = self.call(lambda u: handle(value("URL", "https://p.example/b")),
                                   doi="10.1000/b", cache=cache)
        self.assertEqual(f2.calls, [handle_url("10.1000/b")])
        self.assertEqual(got, "https://p.example/b")


# --------------------------------------------------------------------------- 2. scoring fetch_url

class DoiFetchUrl(unittest.TestCase):
    def test_resolved_target_is_fetch_url(self):
        u = doi_url("a")
        rows, _, _, fake = json_rows([u], net_rule({"a": "https://publisher.example/article/a"}))
        self.assertEqual(rows[u]["url"], u)
        self.assertEqual(rows[u]["fetch_url"], "https://publisher.example/article/a")
        self.assertEqual(fake.to("doi.org"), [handle_url("10.1000/a")])

    def test_recent_arxiv_target_rewritten_to_html(self):
        u = doi_url("a")
        rows, _, _, _ = json_rows([u], net_rule({"a": "https://arxiv.org/abs/2401.01234"}))
        self.assertEqual(rows[u]["url"], u)
        self.assertEqual(rows[u]["fetch_url"], "https://arxiv.org/html/2401.01234")

    def test_old_arxiv_target_kept(self):
        u = doi_url("a")
        rows, _, _, _ = json_rows([u], net_rule({"a": "https://arxiv.org/abs/2301.00001"}))
        self.assertEqual(rows[u]["fetch_url"], "https://arxiv.org/abs/2301.00001")

    def test_subdomain_resolved(self):
        u = doi_url("a", host="dx.doi.org")
        rows, _, _, fake = json_rows([u], net_rule({"a": "https://publisher.example/article/a"}))
        self.assertEqual(rows[u]["url"], u)
        self.assertEqual(rows[u]["fetch_url"], "https://publisher.example/article/a")
        self.assertEqual(fake.to("doi.org"), [handle_url("10.1000/a")])

    def test_not_found_keeps_doi_url(self):
        u = doi_url("a")
        rows, _, _, fake = json_rows([u], net_rule({}))
        self.assertTrue(fake.to("doi.org"))
        self.assertEqual(rows[u]["fetch_url"], u)

    def test_no_url_value_keeps_doi_url(self):
        u = doi_url("a")

        def rule(url):
            if urllib.parse.urlsplit(url).hostname == "doi.org":
                return handle(value("HS_ADMIN", "x", 100))
            return net_rule({})(url)
        rows, _, _, _ = json_rows([u], rule)
        self.assertEqual(rows[u]["fetch_url"], u)

    def test_unreachable_keeps_doi_url(self):
        # One handle failure among six lookups: below the exit-4 threshold.
        urls = [doi_url("a"), doi_url("b"), doi_url("c")]
        rows, err, stats, _ = json_rows(
            urls, net_rule({"a": "https://p.example/a", "b": "https://p.example/b"},
                           handle_fail={"c": urllib.error.URLError("x")}))
        self.assertEqual(stats, {"lookups": 6, "failed": 1})
        self.assertEqual(rows[doi_url("c")]["fetch_url"], doi_url("c"))
        self.assertEqual(rows[doi_url("a")]["fetch_url"], "https://p.example/a")

    def test_no_net_does_not_resolve(self):
        u = doi_url("a")
        rows, _, stats, fake = json_rows([u], lambda x: AssertionError("must not be asked"), "--no-net")
        self.assertEqual(fake.calls, [])
        self.assertEqual(stats, {"lookups": 0, "failed": 0})
        self.assertEqual(rows[u]["fetch_url"], u)

    def test_publisher_url_with_doi_not_resolved(self):
        urls = ["https://link.springer.com/article/10.1007/s00000-020-0001-1",
                "https://onlinelibrary.wiley.com/doi/10.1002/abc.123",
                "https://notdoi.org/10.1000/a"]
        rows, _, _, fake = json_rows(urls, net_rule({"a": "https://p.example/a"}))
        self.assertEqual(fake.to("doi.org"), [])
        for u in urls:
            self.assertEqual(rows[u]["fetch_url"], u)

    def test_table_and_compact_show_fetch_url(self):
        u = doi_url("a")
        target = "https://publisher.example/article/a"
        rows, _, _, _ = json_rows([u], net_rule({"a": target}))
        self.assertIn(rows[u]["verdict"], ("PRIMARY", "SUPPORT"), "compact would hide this row")
        for fmt in ("table", "compact"):
            with self.subTest(format=fmt):
                code, out, err, _, _ = run_cli(["-u", u, "--format", fmt], net_rule({"a": target}))
                self.assertEqual(code, 0, err)
                self.assertIn(target, out)
                self.assertNotIn(u, out)


# --------------------------------------------------------------------------- 3. exit 4 incl. handle lookups

class HandleLookupsCount(unittest.TestCase):
    def test_handle_failures_reach_exit4(self):
        # OpenAlex answers, only the handle API fails: 1 of 2 lookups -> exactly half.
        code, out, err, stats, _ = run_cli(
            ["-u", doi_url("a")], net_rule({}, handle_fail={"a": urllib.error.URLError("x")}))
        self.assertEqual(stats, {"lookups": 2, "failed": 1})
        self.assertEqual(code, 4, err)
        for h in fetchers.LOOKUP_HOSTS:
            self.assertIn(h, err)
        self.assertIn("doi.org", err)
        self.assertNotIn("no-net", err)

    def test_handle_failure_minority_exit0(self):
        urls = [doi_url("a"), doi_url("b"), doi_url("c")]
        args = sum((["-u", u] for u in urls), [])
        code, out, err, stats, _ = run_cli(
            args, net_rule({"a": "https://p.example/a", "b": "https://p.example/b"},
                           handle_fail={"c": _http_error("x", 503)}))
        self.assertEqual(stats, {"lookups": 6, "failed": 1})
        self.assertEqual(code, 0, err)
        self.assertRegex(err, r"flagged\s+`?lookup-failed`?")


# --------------------------------------------------------------------------- 4. README

class ReadmeLists(unittest.TestCase):
    def test_allow_line_lists_doi_org(self):
        with open(os.path.join(ROOT, "README.md"), encoding="utf-8") as f:
            lines = [ln for ln in f.read().splitlines() if "api.openalex.org" in ln]
        self.assertTrue(lines)
        self.assertTrue(any("`doi.org`" in ln for ln in lines), lines)


# --------------------------------------------------------------------------- 5. non_content_host_path_regex

SS_PAPER = "https://www.semanticscholar.org/paper/Title-Slug/0123456789abcdef0123456789abcdef01234567"
SS_AUTHOR = "https://www.semanticscholar.org/author/Some-One/1234567"


def offline_rows(urls, *extra):
    argv = [sys.executable, SRCSCORE, "--no-net", "--format", "json"]
    for u in urls:
        argv += ["-u", u]
    p = subprocess.run(argv + list(extra), capture_output=True, text=True, timeout=60)
    if p.returncode != 0:
        raise AssertionError("CLI exit %d: %s" % (p.returncode, p.stderr))
    return {r["url"]: r for r in json.loads(p.stdout)}


def raw_policy():
    with open(POLICY_JSON, encoding="utf-8") as f:
        return json.load(f)


class HostPathNonContent(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.points = raw_policy()["penalties"]["non_content_path"]["points"]

    def test_policy_holds_semantic_scholar_paper(self):
        self.assertEqual(raw_policy()["non_content_host_path_regex"].get("semanticscholar.org"), ["^/paper/"])

    def test_paper_page_drops_one_band(self):
        r = offline_rows([SS_PAPER, SS_AUTHOR])
        paper, author = r[SS_PAPER], r[SS_AUTHOR]
        self.assertEqual(str(paper["tier"]), "2")
        self.assertIn("non-content", paper["flags"])
        self.assertEqual(paper["score"], 46.0)
        self.assertEqual(paper["verdict"], "SKIM")
        self.assertEqual(author["score"], 74.0)
        self.assertEqual(paper["score"] - author["score"], self.points)

    def test_bare_domain_paper_page(self):
        u = "https://semanticscholar.org/paper/Title-Slug/0123456789abcdef0123456789abcdef01234567"
        self.assertIn("non-content", offline_rows([u])[u]["flags"])

    def test_unaffected(self):
        urls = [SS_AUTHOR,
                "https://papers.nips.cc/paper/2020/hash/0123456789abcdef-Abstract.html",
                "https://example.org/paper/some-paper",
                "https://notsemanticscholar.org/paper/x"]
        r = offline_rows(urls)
        for u in urls:
            with self.subTest(url=u):
                self.assertNotIn("non-content", r[u]["flags"])

    def test_applied_once_when_both_rules_match(self):
        p = raw_policy()
        p["non_content_path_regex"] = list(p["non_content_path_regex"]) + ["^/paper/"]
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "policy.json")
            with open(path, "w", encoding="utf-8") as f:
                json.dump(p, f)
            both = offline_rows([SS_PAPER], "--policy", path)[SS_PAPER]
        self.assertEqual(both["flags"].count("non-content"), 1)
        self.assertEqual(both["score"], 46.0)

    def test_custom_domain_and_subdomain(self):
        p = raw_policy()
        p["non_content_host_path_regex"] = {"example.org": ["^/listing/"]}
        urls = ["https://example.org/listing/1", "https://docs.example.org/listing/2",
                "https://example.com/listing/3", "https://example.org/article/4"]
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "policy.json")
            with open(path, "w", encoding="utf-8") as f:
                json.dump(p, f)
            r = offline_rows(urls, "--policy", path)
            base = offline_rows(urls)
        for u in urls[:2]:
            self.assertIn("non-content", r[u]["flags"], u)
            self.assertEqual(r[u]["score"] - base[u]["score"], self.points, u)
        for u in urls[2:]:
            self.assertNotIn("non-content", r[u]["flags"], u)
            self.assertEqual(r[u]["score"], base[u]["score"], u)


class HostPathValidation(unittest.TestCase):
    def setUp(self):
        self.policy = load_policy()

    def with_key(self, v):
        p = copy.deepcopy(self.policy)
        p["non_content_host_path_regex"] = v
        return p

    def test_real_policy_valid(self):
        validate_policy(self.policy)

    def test_valid_value_accepted(self):
        validate_policy(self.with_key({"example.org": ["^/a/", "b$"]}))

    def test_rejected(self):
        for name, v in (("list", ["^/paper/"]), ("string", "^/paper/"),
                        ("value string", {"example.org": "^/paper/"}),
                        ("value dict", {"example.org": {"x": "^/paper/"}}),
                        ("entry int", {"example.org": [1]}),
                        ("entry none", {"example.org": ["^/a/", None]}),
                        ("bad regex", {"example.org": ["("]})):
            with self.subTest(name):
                with self.assertRaises(PolicyError):
                    validate_policy(self.with_key(v))

    def test_check_policy_passes(self):
        p = subprocess.run([sys.executable, CHECK_POLICY], capture_output=True, text=True, timeout=120)
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)


# --------------------------------------------------------------------------- 6. policy-eval aliases

DOI = "https://doi.org/10.1000/a"
TARGET = "https://publisher.example/article/a"
OTHER = "https://other.example/x"


def scores_fixture():
    n = arms.norm
    return {n(DOI): {"url": DOI, "fetch_url": TARGET, "tier": "2", "verdict": "SUPPORT"},
            n(OTHER): {"url": OTHER, "fetch_url": OTHER, "tier": "3", "verdict": "SKIM"},
            n("https://arxiv.org/abs/2401.01234"): {"url": "https://arxiv.org/abs/2401.01234",
                                                    "fetch_url": "https://arxiv.org/html/2401.01234",
                                                    "tier": "2", "verdict": "SUPPORT"},
            n("https://nofetch.example/y"): {"url": "https://nofetch.example/y", "tier": "3", "verdict": "SKIM"}}


def ev_fixture():
    return {"searches": [{"role": "main", "query": "q", "summary_chars": 0,
                          "links": [{"url": DOI}, {"url": OTHER}]}],
            "fetches": [{"role": "main", "url": TARGET, "ok": True, "code": 200, "error": None}],
            "score_runs": [{"role": "main", "exit": 0, "chars": 10, "lookups_failed": None,
                            "rows": [{"url": TARGET, "verdict": "SUPPORT"},
                                     {"url": OTHER, "verdict": "SKIM"}]}],
            "agent_returns": [{"tool_use_id": "j", "chars": 5, "kind": "judge",
                               "verdicts": [{"url": TARGET, "verdict": "USE", "kind": ""}]}],
            "skill_fired": True, "usage_by_role": [], "result": {}, "main_peak_context": 0,
            "report_chars": 0, "closing": None, "url_files": []}


class FetchAliases(unittest.TestCase):
    def test_only_rows_with_other_address(self):
        n = arms.norm
        self.assertEqual(arms.fetch_aliases(scores_fixture()), {n(TARGET): n(DOI)})

    def test_empty_scores(self):
        self.assertEqual(arms.fetch_aliases({}), {})

    def test_arm_urls_with_alias(self):
        n = arms.norm
        lvl = arms.arm_urls(ev_fixture(), arms.fetch_aliases(scores_fixture()))
        self.assertEqual(lvl.get(n(DOI)), "read")
        self.assertNotIn(n(TARGET), lvl)

    def test_arm_urls_without_alias(self):
        n = arms.norm
        lvl = arms.arm_urls(ev_fixture())
        self.assertEqual(lvl.get(n(TARGET)), "read")
        self.assertEqual(lvl.get(n(DOI)), "listed")

    def test_in_run_verdicts_with_alias(self):
        n = arms.norm
        v = arms.in_run_verdicts(ev_fixture(), arms.fetch_aliases(scores_fixture()))
        self.assertEqual(v.get(n(DOI)), "SUPPORT/USE")
        self.assertNotIn(n(TARGET), v)
        self.assertEqual(v.get(n(OTHER)), "SKIM")

    def test_in_run_verdicts_without_alias(self):
        n = arms.norm
        v = arms.in_run_verdicts(ev_fixture())
        self.assertEqual(v.get(n(TARGET)), "SUPPORT/USE")
        self.assertNotIn(n(DOI), v)

    def test_analyze_arm_counts_under_doi_url(self):
        n = arms.norm
        labels = {n(DOI): {"judgment": "useful"}, n(OTHER): {"judgment": "junk"}}
        out = arms.analyze_arm(ev_fixture(), labels, scores_fixture(),
                               {"id": "t", "mode": "academic", "field": "ai"})
        self.assertEqual(out["read_urls"], [n(DOI)])
        self.assertEqual(out["sources_read"]["main_ok"], 1)
        self.assertEqual(out["sources_read"]["main_by_label"], {"useful": 1})
        self.assertEqual(out["contamination"]["read"]["useful"], {"2": 1})
        self.assertEqual(out["judge_vs_blind"], {"USE": {"useful": 1}})
        self.assertEqual(out["main_reads_by_in_run_verdict"], {"SUPPORT/USE": 1})
        self.assertIn(n(DOI), out["useful_reached"])

    def test_analyze_arm_no_alias_rows(self):
        # A score set with no differing fetch address leaves the read under its own URL.
        n = arms.norm
        scores = {n(OTHER): {"url": OTHER, "fetch_url": OTHER, "tier": "3"}}
        out = arms.analyze_arm(ev_fixture(), {}, scores, {"id": "t", "mode": "academic", "field": "ai"})
        self.assertEqual(out["read_urls"], [n(TARGET)])
        self.assertEqual(out["main_reads_by_in_run_verdict"], {"SUPPORT/USE": 1})


if __name__ == "__main__":
    unittest.main()
