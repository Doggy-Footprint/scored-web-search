"""Independent tests for the lookup-failure contract: LOOKUP_HOSTS, exit 4 when most
lookups cannot reach their APIs, `lookup-failed` rows below that threshold, the
`--no-net` help text, and the SKILL.md / README.md wording. Written from the spec.
doi.org counts as a lookup host: doi.org URLs are resolved through its handle API.

Nothing here touches the network: in-process runs patch `urllib.request.urlopen`,
and the subprocess runs point every request at a closed local proxy port."""

import contextlib
import io
import json
import os
import re
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

ROOT = _pathsetup.ROOT
SRCSCORE = os.path.join(_pathsetup.SCRIPTS, "srcscore.py")
FETCHERS_PY = os.path.join(_pathsetup.SCRIPTS, "srcscore_core", "fetchers.py")
SKILL_MD = os.path.join(ROOT, "skills", "scored-web-search", "SKILL.md")
README_MD = os.path.join(ROOT, "README.md")

EXPECTED_HOSTS = {"api.openalex.org", "api.semanticscholar.org", "api.github.com", "hn.algolia.com",
                  "doi.org"}
DEAD_PROXY = "http://127.0.0.1:9"   # discard port; nothing listens, so connect is refused


def read(path):
    with open(path, encoding="utf-8") as f:
        return f.read()


def squash(text):
    return re.sub(r"\s+", " ", text)


def section(md, heading_re):
    """Body of the markdown section whose heading matches, up to the next heading of the
    same or higher level."""
    m = re.search(r"^(#+)\s*" + heading_re + r".*$", md, re.M)
    if not m:
        raise AssertionError("no section matching %r" % heading_re)
    level = len(m.group(1))
    rest = md[m.end():]
    nxt = re.search(r"^#{1,%d}\s" % level, rest, re.M)
    return rest[: nxt.start()] if nxt else rest


# --------------------------------------------------------------------------- fake network

class _Resp:
    def __init__(self, payload, status=200):
        self.status = status
        self._body = json.dumps(payload).encode("utf-8")

    def read(self):
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def _http_error(url, code):
    return urllib.error.HTTPError(url, code, "status %d" % code, {}, None)


OA_RECORD = {"id": "https://openalex.org/W1", "display_name": "A paper", "cited_by_count": 10,
             "publication_year": 2020, "publication_date": "2020-01-01", "is_retracted": False,
             "primary_location": {"source": {"display_name": "J", "type": "journal"}},
             "locations": [{"source": {"display_name": "J", "type": "journal"}}]}


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

    def hosts(self):
        return {urllib.parse.urlsplit(u).hostname for u in self.calls}


def run_cli(args, rule):
    """Runs cli.main in-process with a fake network and an empty cache.
    Returns (exit code, stdout, stderr, stats, fake)."""
    fake = FakeNet(rule)
    out, err = io.StringIO(), io.StringIO()
    saved = dict(fetchers.STATS)
    with tempfile.TemporaryDirectory() as cache_dir, \
            mock.patch("urllib.request.urlopen", fake), \
            mock.patch.object(cli, "CACHE_DIR", cache_dir), \
            contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        fetchers.STATS["lookups"] = 0
        fetchers.STATS["failed"] = 0
        try:
            code = cli.main(list(args) + ["--workers", "1"])
            stats = dict(fetchers.STATS)
        finally:
            fetchers.STATS.clear()
            fetchers.STATS.update(saved)
    return code, out.getvalue(), err.getvalue(), stats, fake


def run_sub(*args, extra_env=None):
    env = {k: v for k, v in os.environ.items() if k.lower() not in ("no_proxy",)}
    env.update({"HTTPS_PROXY": DEAD_PROXY, "https_proxy": DEAD_PROXY,
                "HTTP_PROXY": DEAD_PROXY, "http_proxy": DEAD_PROXY})
    env.update(extra_env or {})
    return subprocess.run([sys.executable, SRCSCORE, *args], capture_output=True, text=True,
                          env=env, timeout=120)


def doi_url(suffix):
    return "https://doi.org/10.1000/" + suffix


def handle_record(target):
    return {"responseCode": 1, "handle": "10.1000/x",
            "values": [{"index": 1, "type": "URL", "data": {"format": "string", "value": target}}]}


def oa_rule(fail_marker, failure, handle_fails=False):
    """OpenAlex answers a record for every DOI except those containing `fail_marker`,
    which get `failure`. The doi.org handle API resolves every DOI to a publisher page,
    except that with `handle_fails` the `fail_marker` DOIs get `failure` there too.
    Other hosts answer an empty-but-valid body."""
    def rule(url):
        host = urllib.parse.urlsplit(url).hostname
        if host == "api.openalex.org":
            return failure if fail_marker in url else OA_RECORD
        if host == "doi.org":
            if handle_fails and fail_marker in url:
                return failure
            return handle_record("https://publisher.example/article/1")
        if host == "hn.algolia.com":
            return {"hits": []}
        return _http_error(url, 404)
    return rule


# --------------------------------------------------------------------------- 1. LOOKUP_HOSTS

class LookupHostsMatchFetchers(unittest.TestCase):
    def test_exact_set(self):
        self.assertEqual(set(fetchers.LOOKUP_HOSTS), EXPECTED_HOSTS)
        self.assertEqual(len(fetchers.LOOKUP_HOSTS), len(set(fetchers.LOOKUP_HOSTS)))

    def test_cli_uses_same_object(self):
        self.assertIs(cli.LOOKUP_HOSTS, fetchers.LOOKUP_HOSTS)

    def test_static_request_hosts_in_source(self):
        src = read(FETCHERS_PY)
        # A request URL is a string literal that starts with the scheme.
        hosts = set(re.findall(r"""["']https?://([A-Za-z0-9.-]+)""", src))
        self.assertEqual(hosts, set(fetchers.LOOKUP_HOSTS))

    def test_doi_org_contacted_through_handle_api(self):
        # doi.org is a lookup host in its own right now: its handle API, not only
        # text inside an OpenAlex path.
        fake = FakeNet(lambda u: urllib.error.URLError("unreachable"))
        saved = dict(fetchers.STATS)
        try:
            with mock.patch("urllib.request.urlopen", fake):
                fetchers.doi_target("10.1000/x", fetchers.NullCache(), 1)
        finally:
            fetchers.STATS.clear()
            fetchers.STATS.update(saved)
        self.assertTrue(fake.calls)
        for u in fake.calls:
            p = urllib.parse.urlsplit(u)
            self.assertEqual(p.hostname, "doi.org")
            self.assertTrue(p.path.startswith("/api/handles/"), u)

    def test_dynamic_hosts_contacted(self):
        fake = FakeNet(lambda u: urllib.error.URLError("unreachable"))
        saved = dict(fetchers.STATS)
        cache = fetchers.NullCache()
        try:
            with mock.patch("urllib.request.urlopen", fake):
                fetchers.openalex_by_doi("10.1000/x", cache, 1)
                fetchers.openalex_by_pmid("123", cache, 1)
                fetchers.s2_by_arxiv("2401.01234", cache, 1)
                fetchers.arxiv_lookup("2401.01234", cache, 1)
                fetchers.github_repo("o", "r", cache, 1)
                fetchers.hn_item("1", cache, 1)
                fetchers.hn_points("https://example.com/a", cache, 1)
                fetchers.doi_target("10.1000/x", cache, 1)
        finally:
            fetchers.STATS.clear()
            fetchers.STATS.update(saved)
        self.assertEqual(fake.hosts(), set(fetchers.LOOKUP_HOSTS))


# --------------------------------------------------------------------------- 2. exit 4

class MostLookupsFailExit4(unittest.TestCase):
    FAILURES = {
        "unreachable": urllib.error.URLError("unreachable"),
        "refused": ConnectionRefusedError("refused"),
        "timeout": socket.timeout("timed out"),
        "401": 401, "403": 403, "407": 407, "429": 429, "500": 500, "502": 502, "503": 503,
    }

    def check_exit4(self, code, err):
        self.assertEqual(code, 4, err)
        for h in fetchers.LOOKUP_HOSTS:
            self.assertIn(h, err)
        self.assertRegex(err.lower(), r"allow network access")
        self.assertRegex(err.lower(), r"run again")
        self.assertNotIn("no-net", err)

    def test_every_failure_kind(self):
        for name, f in self.FAILURES.items():
            with self.subTest(failure=name):
                def rule(url, f=f):
                    return _http_error(url, f) if isinstance(f, int) else f
                code, out, err, stats, _ = run_cli(
                    ["-u", doi_url("a"), "-u", doi_url("b"), "-u", "https://github.com/o/r"], rule)
                self.assertGreater(stats["lookups"], 0)
                self.assertEqual(stats["failed"], stats["lookups"])
                self.check_exit4(code, err)

    # Each doi.org URL costs two lookups: OpenAlex by DOI and the doi.org handle API.

    def test_exactly_half_is_exit4(self):
        code, out, err, stats, _ = run_cli(
            ["-u", doi_url("ok1"), "-u", doi_url("fail1")],
            oa_rule("fail", urllib.error.URLError("unreachable"), handle_fails=True))
        self.assertEqual(stats, {"lookups": 4, "failed": 2})
        self.check_exit4(code, err)

    def test_majority_is_exit4(self):
        code, out, err, stats, _ = run_cli(
            ["-u", doi_url("ok1"), "-u", doi_url("fail1"), "-u", doi_url("fail2")],
            oa_rule("fail", _http_error("x", 503), handle_fails=True))
        self.assertEqual(stats, {"lookups": 6, "failed": 4})
        self.check_exit4(code, err)

    def test_subprocess_dead_proxy(self):
        with tempfile.TemporaryDirectory() as cache_dir:
            p = run_sub("-u", doi_url("a"), "-u", "https://news.ycombinator.com/item?id=1",
                        "--workers", "1", extra_env={"SRCSCORE_CACHE": cache_dir})
        self.check_exit4(p.returncode, p.stderr)


# --------------------------------------------------------------------------- 3. below threshold

class FewLookupsFailFlagged(unittest.TestCase):
    def test_minority_flags_rows(self):
        urls = [doi_url("ok1"), doi_url("ok2"), doi_url("fail1")]
        args = sum((["-u", u] for u in urls), []) + ["--format", "json"]
        code, out, err, stats, _ = run_cli(args, oa_rule("fail", urllib.error.URLError("x")))
        self.assertEqual(stats, {"lookups": 6, "failed": 1})   # 3 OpenAlex + 3 handle API
        self.assertNotEqual(code, 4, err)
        self.assertEqual(code, 0, err)
        self.assertRegex(err, r"flagged\s+`?lookup-failed`?")
        self.assertNotIn("no-net", err)
        rows = {r["url"]: r for r in json.loads(out)}
        self.assertIn("lookup-failed", rows[doi_url("fail1")]["flags"])
        for u in (doi_url("ok1"), doi_url("ok2")):
            self.assertNotIn("lookup-failed", rows[u]["flags"])

    def test_minority_http_status_flags_rows(self):
        for status in (401, 403, 407, 429, 500):
            with self.subTest(status=status):
                urls = [doi_url("ok1"), doi_url("ok2"), doi_url("ok3"), doi_url("fail1")]
                args = sum((["-u", u] for u in urls), []) + ["--format", "json"]
                code, out, err, stats, _ = run_cli(args, oa_rule("fail", _http_error("x", status)))
                self.assertEqual(stats, {"lookups": 8, "failed": 1})   # 4 OpenAlex + 4 handle API
                self.assertEqual(code, 0, err)
                self.assertRegex(err, r"flagged\s+`?lookup-failed`?")
                rows = {r["url"]: r for r in json.loads(out)}
                self.assertIn("lookup-failed", rows[doi_url("fail1")]["flags"])

    def test_table_output_shows_flag(self):
        code, out, err, _, _ = run_cli(
            ["-u", doi_url("ok1"), "-u", doi_url("ok2"), "-u", doi_url("fail1")],
            oa_rule("fail", urllib.error.URLError("x")))
        self.assertEqual(code, 0, err)
        self.assertIn("lookup-failed", out)

    def test_404_is_not_a_transport_failure(self):
        code, out, err, stats, _ = run_cli(
            ["-u", doi_url("a"), "-u", doi_url("b"), "--format", "json"],
            lambda u: _http_error(u, 404))
        self.assertGreater(stats["lookups"], 0)
        self.assertEqual(stats["failed"], 0)
        self.assertEqual(code, 0, err)
        self.assertNotIn("lookup-failed", err)
        for r in json.loads(out):
            self.assertNotIn("lookup-failed", r["flags"])


# --------------------------------------------------------------------------- 4. --no-net

class NoNetStillWorks(unittest.TestCase):
    def test_in_process_no_lookups(self):
        code, out, err, stats, fake = run_cli(
            ["--no-net", "-u", doi_url("a"), "-u", "https://github.com/o/r", "--format", "json"],
            lambda u: urllib.error.URLError("must not be called"))
        self.assertEqual(code, 0, err)
        self.assertEqual(fake.calls, [])
        self.assertEqual(stats, {"lookups": 0, "failed": 0})
        for r in json.loads(out):
            self.assertNotIn("lookup-failed", r["flags"])

    def test_subprocess_dead_proxy_exit0(self):
        with tempfile.TemporaryDirectory() as cache_dir:
            p = run_sub("--no-net", "-u", doi_url("a"), "-u", "https://news.ycombinator.com/item?id=1",
                        extra_env={"SRCSCORE_CACHE": cache_dir})
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertNotIn("could not be reached", p.stderr)

    def test_help_marks_offline_only(self):
        p = run_sub("--help")
        self.assertEqual(p.returncode, 0, p.stderr)
        h = squash(p.stdout)
        m = re.search(r"--no-net\s+(.*?)(?=\s--[a-z])", h)
        self.assertIsNotNone(m, h)
        text = m.group(1).lower()
        self.assertIn("offline testing only", text)
        self.assertIn("not what a research run should act on", text)


# --------------------------------------------------------------------------- 5. SKILL.md error handling

class SkillErrorHandling(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.text = squash(section(read(SKILL_MD), r"Error handling")).lower()

    def test_report_and_stop_no_unscored(self):
        self.assertRegex(self.text, r"fails, report it and stop")
        self.assertRegex(self.text, r"(do not|don't) fall back to unscored reading")

    def test_exit4_meaning(self):
        self.assertRegex(self.text, r"exit 4 means [^.]*could not reach their apis")

    def test_tell_hosts_and_stop(self):
        self.assertRegex(self.text, r"tell the user the hosts to allow[^.]*and stop")

    def test_no_rerun_no_net(self):
        self.assertRegex(self.text, r"(do not|don't) re-run with `?--no-net`?")


# --------------------------------------------------------------------------- 6. SKILL.md Step 1 paths

class SkillStep1Paths(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.md = read(SKILL_MD)
        cls.step1 = section(cls.md, r"Step 1\b")

    def test_path_named_under_tmpdir(self):
        s = squash(self.step1)
        self.assertRegex(s, r"file path you name under `?\$TMPDIR`?")
        self.assertRegex(s, r"`\$TMPDIR/[^`]+\.json`")

    def test_tmp_only_when_unset(self):
        s = squash(self.step1)
        self.assertRegex(s, r"`?/tmp`? only when (it|\$TMPDIR) is unset")

    def test_no_subagent_fallback_uses_tmpdir(self):
        paras = [p for p in re.split(r"\n\s*\n", self.step1) if "No sub agent fallback" in p]
        self.assertEqual(len(paras), 1, paras)
        p = squash(paras[0])
        self.assertIn("urls.json", p)
        self.assertRegex(p, r"urls\.json`? under `?\$TMPDIR")

    def test_no_plain_tmp_default_anywhere(self):
        # No file path under /tmp is given as an instruction anywhere in SKILL.md.
        self.assertIsNone(re.search(r"(?<![\w$])/tmp/\S", self.md),
                          "SKILL.md names a path under plain /tmp")
        for line in self.md.splitlines():
            for m in re.finditer(r"(?<![\w$])/tmp\b", line):
                with self.subTest(line=line):
                    self.assertIn("$TMPDIR", line,
                                  "bare /tmp mention not qualified by $TMPDIR")
                    self.assertRegex(line, r"only when it is unset|not in `?/tmp")


# --------------------------------------------------------------------------- 7. README hosts

class ReadmeHosts(unittest.TestCase):
    HOST_RE = r"`([a-z0-9-]+(?:\.[a-z0-9-]+)+)`"

    @classmethod
    def setUpClass(cls):
        cls.md = read(README_MD)
        cls.allow_lines = [ln for ln in cls.md.splitlines()
                           if ("허용" in ln or re.search(r"\ballow", ln, re.I))
                           and re.search(cls.HOST_RE, ln)]

    def test_lists_exactly_lookup_hosts(self):
        self.assertTrue(self.allow_lines, "README has no line listing hosts to allow")
        hosts = set()
        for ln in self.allow_lines:
            hosts |= {h for h in re.findall(self.HOST_RE, ln) if not h.endswith((".json", ".md", ".py"))}
        self.assertEqual(hosts, set(fetchers.LOOKUP_HOSTS))

    def test_lists_doi_org(self):
        self.assertTrue(any(re.search(r"`doi\.org`", ln) for ln in self.allow_lines),
                        "README's hosts-to-allow line does not name doi.org")


if __name__ == "__main__":
    unittest.main()
