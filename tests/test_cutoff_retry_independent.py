"""Independent tests for the cut-off retry in `fetchers.http_json`: a reply cut off
mid-body (`http.client.IncompleteRead`, any other `http.client.HTTPException`, or
`ConnectionResetError`) is asked for exactly once more; every other failure gets one
attempt. Written from the spec.

Nothing here touches the network: every run patches `urllib.request.urlopen`."""

import contextlib
import http.client
import io
import json
import socket
import tempfile
import unittest
import urllib.error
import urllib.parse
from collections import Counter
from unittest import mock

import _pathsetup  # noqa: F401
from srcscore_core import cli, fetchers

URL = "https://api.openalex.org/works/W1"
PAYLOAD = {"id": "https://openalex.org/W1", "ok": True}

OA_RECORD = {"id": "https://openalex.org/W1", "display_name": "A paper", "cited_by_count": 10,
             "publication_year": 2020, "publication_date": "2020-01-01", "is_retracted": False,
             "primary_location": {"source": {"display_name": "J", "type": "journal"}},
             "locations": [{"source": {"display_name": "J", "type": "journal"}}]}
GH_RECORD = {"full_name": "o/r", "stargazers_count": 50, "forks_count": 5, "archived": False,
             "pushed_at": "2024-01-01T00:00:00Z", "created_at": "2020-01-01T00:00:00Z"}


# --------------------------------------------------------------------------- fake network

class CutOff:
    """Marks a reply whose headers arrive but whose body read raises `exc`."""

    def __init__(self, exc):
        self.exc = exc


class _Resp:
    def __init__(self, payload, status=200, read_exc=None):
        self.status = status
        self._body = json.dumps(payload).encode("utf-8")
        self._read_exc = read_exc

    def read(self, *a, **kw):
        if self._read_exc is not None:
            raise self._read_exc
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def _http_error(url, code):
    return urllib.error.HTTPError(url, code, "status %d" % code, {}, None)


def _incomplete():
    return http.client.IncompleteRead(b'{"id": "htt', 200)


class FakeNet:
    """Stands in for urllib.request.urlopen. `rule(url, n)` gets the 0-based attempt
    number for that URL and returns a payload, a CutOff, or an exception to raise from
    urlopen itself. Every requested URL is recorded."""

    def __init__(self, rule):
        self.rule = rule
        self.calls = []

    def __call__(self, req, timeout=None, *a, **kw):
        url = req.full_url if hasattr(req, "full_url") else str(req)
        n = self.calls.count(url)
        self.calls.append(url)
        out = self.rule(url, n)
        if isinstance(out, CutOff):
            return _Resp({}, read_exc=out.exc)
        if isinstance(out, BaseException):
            raise out
        return _Resp(out)

    def per_url(self):
        return Counter(self.calls)


def sequence(*steps):
    """Rule that plays `steps` in order per attempt; attempts past the end fail loudly."""
    def rule(url, n):
        if n < len(steps):
            s = steps[n]
            return s() if callable(s) else s
        return AssertionError("unexpected attempt %d for %s" % (n + 1, url))
    return rule


def call_http_json(rule, url=URL):
    """Runs http_json once against a fake network with STATS zeroed.
    Returns (result, stats, fake)."""
    fake = FakeNet(rule)
    saved = dict(fetchers.STATS)
    try:
        fetchers.STATS["lookups"] = 0
        fetchers.STATS["failed"] = 0
        with mock.patch("urllib.request.urlopen", fake):
            result = fetchers.http_json(url, 1)
        stats = dict(fetchers.STATS)
    finally:
        fetchers.STATS.clear()
        fetchers.STATS.update(saved)
    return result, stats, fake


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


CUTOFF_KINDS = {
    "IncompleteRead": _incomplete,
    "RemoteDisconnected": lambda: http.client.RemoteDisconnected("closed mid-body"),
    "BadStatusLine": lambda: http.client.BadStatusLine("garbage"),
    "plain HTTPException": lambda: http.client.HTTPException("cut"),
    "ConnectionResetError": lambda: ConnectionResetError(54, "reset by peer"),
}


# --------------------------------------------------------------------------- 1. cut once, retry ok

class CutOffOnceThenOk(unittest.TestCase):
    def test_every_cutoff_kind_retries_and_succeeds(self):
        for name, make in CUTOFF_KINDS.items():
            with self.subTest(cutoff=name):
                result, stats, fake = call_http_json(
                    sequence(lambda make=make: CutOff(make()), PAYLOAD))
                self.assertEqual(result, PAYLOAD)
                self.assertEqual(fake.calls, [URL, URL], "retry must re-request the same URL")
                self.assertEqual(stats, {"lookups": 1, "failed": 0})

    def test_retry_uses_same_url_with_query(self):
        url = "https://api.semanticscholar.org/graph/v1/paper/arXiv:2401.01234?fields=title,year"
        result, stats, fake = call_http_json(sequence(CutOff(_incomplete()), PAYLOAD), url=url)
        self.assertEqual(result, PAYLOAD)
        self.assertEqual(fake.calls, [url, url])
        self.assertEqual(stats, {"lookups": 1, "failed": 0})


# --------------------------------------------------------------------------- 2. cut twice

class CutOffTwice(unittest.TestCase):
    def test_every_cutoff_kind_twice_is_fetch_failed(self):
        for name, make in CUTOFF_KINDS.items():
            with self.subTest(cutoff=name):
                result, stats, fake = call_http_json(
                    lambda url, n, make=make: CutOff(make()))
                self.assertIs(result, fetchers.FETCH_FAILED)
                self.assertEqual(len(fake.calls), 2, "never more than two attempts")
                self.assertEqual(stats, {"lookups": 1, "failed": 1})

    def test_mixed_cutoff_kinds_twice(self):
        result, stats, fake = call_http_json(
            sequence(CutOff(_incomplete()), CutOff(ConnectionResetError("reset"))))
        self.assertIs(result, fetchers.FETCH_FAILED)
        self.assertEqual(fake.calls, [URL, URL])
        self.assertEqual(stats, {"lookups": 1, "failed": 1})

    def test_third_attempt_would_succeed_but_is_never_made(self):
        result, stats, fake = call_http_json(
            sequence(CutOff(_incomplete()), CutOff(_incomplete()), PAYLOAD))
        self.assertIs(result, fetchers.FETCH_FAILED)
        self.assertEqual(len(fake.calls), 2)
        self.assertEqual(stats, {"lookups": 1, "failed": 1})


# --------------------------------------------------------------------------- 3. no retry for others

class OtherFailuresNotRetried(unittest.TestCase):
    def check_one(self, failure, expect_result, expect_failed):
        result, stats, fake = call_http_json(sequence(failure, PAYLOAD))
        if expect_result is fetchers.FETCH_FAILED:
            self.assertIs(result, fetchers.FETCH_FAILED)
        else:
            self.assertEqual(result, expect_result)
        self.assertEqual(fake.calls, [URL], "must not retry")
        self.assertEqual(stats, {"lookups": 1, "failed": expect_failed})

    def test_404_is_none_counted_not_failed(self):
        self.check_one(_http_error(URL, 404), None, 0)

    def test_410_is_none_counted_not_failed(self):
        self.check_one(_http_error(URL, 410), None, 0)

    def test_refused_or_throttled_status_is_fetch_failed(self):
        for code in (401, 403, 407, 429, 500, 502, 503):
            with self.subTest(status=code):
                self.check_one(_http_error(URL, code), fetchers.FETCH_FAILED, 1)

    def test_url_error(self):
        self.check_one(urllib.error.URLError("unreachable"), fetchers.FETCH_FAILED, 1)

    def test_url_error_wrapping_reset_on_connect(self):
        # urllib wraps connect-time socket errors in URLError; that is not a cut-off body.
        self.check_one(urllib.error.URLError(ConnectionResetError("reset")),
                       fetchers.FETCH_FAILED, 1)

    def test_timeout_on_open(self):
        for exc in (socket.timeout("timed out"), TimeoutError("timed out")):
            with self.subTest(exc=type(exc).__name__):
                self.check_one(exc, fetchers.FETCH_FAILED, 1)

    def test_timeout_while_reading_body(self):
        for exc in (socket.timeout("timed out"), TimeoutError("timed out")):
            with self.subTest(exc=type(exc).__name__):
                self.check_one(CutOff(exc), fetchers.FETCH_FAILED, 1)

    def test_connection_refused(self):
        self.check_one(ConnectionRefusedError("refused"), fetchers.FETCH_FAILED, 1)


# --------------------------------------------------------------------------- 4. plain success

class PlainSuccess(unittest.TestCase):
    def test_one_attempt_returns_json(self):
        result, stats, fake = call_http_json(sequence(PAYLOAD))
        self.assertEqual(result, PAYLOAD)
        self.assertEqual(fake.calls, [URL])
        self.assertEqual(stats, {"lookups": 1, "failed": 0})


# --------------------------------------------------------------------------- 5. through the CLI

ARGS = ["-u", "https://doi.org/10.1000/a", "-u", "https://doi.org/10.1000/b",
        "-u", "https://github.com/o/r", "--format", "json"]


def answer_for(url):
    host = urllib.parse.urlsplit(url).hostname
    if host == "api.openalex.org":
        return OA_RECORD
    if host == "api.github.com":
        return GH_RECORD
    if host == "hn.algolia.com":
        return {"hits": []}
    return _http_error(url, 404)


class ThroughCli(unittest.TestCase):
    def test_first_read_cut_retry_ok_exits_0(self):
        def rule(url, n):
            return CutOff(_incomplete()) if n == 0 else answer_for(url)
        code, out, err, stats, fake = run_cli(ARGS, rule)
        self.assertEqual(code, 0, err)
        self.assertNotIn("could not be reached", err)
        self.assertGreater(stats["lookups"], 0)
        self.assertEqual(stats["failed"], 0)
        self.assertTrue(fake.per_url())
        for u, n in fake.per_url().items():
            self.assertEqual(n, 2, "each lookup URL asked exactly twice: %s" % u)
        self.assertEqual(stats["lookups"], len(fake.per_url()))
        for r in json.loads(out):
            self.assertNotIn("lookup-failed", r["flags"])

    def test_first_read_reset_retry_ok_exits_0(self):
        def rule(url, n):
            return CutOff(ConnectionResetError("reset")) if n == 0 else answer_for(url)
        code, out, err, stats, fake = run_cli(ARGS, rule)
        self.assertEqual(code, 0, err)
        self.assertNotIn("could not be reached", err)
        self.assertEqual(stats["failed"], 0)

    def test_every_lookup_cut_twice_exits_4(self):
        code, out, err, stats, fake = run_cli(ARGS, lambda url, n: CutOff(_incomplete()))
        self.assertEqual(code, 4, err)
        self.assertIn("could not be reached", err)
        for h in fetchers.LOOKUP_HOSTS:
            self.assertIn(h, err)
        self.assertRegex(err.lower(), r"allow network access")
        self.assertGreater(stats["lookups"], 0)
        self.assertEqual(stats["failed"], stats["lookups"])
        for u, n in fake.per_url().items():
            self.assertEqual(n, 2, "never more than two attempts: %s" % u)
        self.assertEqual(stats["lookups"], len(fake.per_url()))


if __name__ == "__main__":
    unittest.main()
