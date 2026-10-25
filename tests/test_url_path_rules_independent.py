"""Independent black-box tests for the URL-path penalty rules:
non-content, registry stale-version, and --doc-variant / wrong-variant."""

import json
import os
import subprocess
import sys
import unittest

import _pathsetup  # noqa: F401

CLI = os.path.join(_pathsetup.SCRIPTS, "srcscore.py")
MODES = ["academic", "non-academic", "community-opinion", "news", "official-docs"]


def run(urls, *extra, mode=None):
    argv = [sys.executable, CLI, "--no-net", "--format", "json"]
    if mode:
        argv += ["--mode", mode]
    for u in urls:
        argv += ["-u", u]
    argv += list(extra)
    return subprocess.run(argv, capture_output=True, text=True, timeout=60)


def rows(urls, *extra, mode=None):
    p = run(urls, *extra, mode=mode)
    if p.returncode != 0:
        raise AssertionError("CLI exit %d: %s" % (p.returncode, p.stderr))
    return {r["url"]: r for r in json.loads(p.stdout)}


def flags(url, *extra, mode=None):
    return rows([url], *extra, mode=mode)[url]["flags"]


class NonContentTests(unittest.TestCase):
    PENALISED = [
        "https://par.nsf.gov/search/author:%22X%22",
        "https://example.org/search",
        "https://example.org/SEARCH/things",
        "https://github.com/o/r/pull/1.diff",
        "https://github.com/o/r/commit/abc.patch",
        "https://example.org/pkg/mod.cpython-311.pyc",
        "https://example.org/repo/__pycache__/x.html",
        "https://example.org/lib/python3.11/site-packages/foo/bar.py",
        "https://example.org/home/u/anaconda3/lib/x.html",
        "https://example.org/proj/node_modules/left-pad/index.js",
        "https://example.org/search?q=x#frag",
    ]
    CLEAN = [
        "https://learn.microsoft.com/en-us/azure/search/search-what-is-azure-search",
        "https://example.org/page?x=/search/",
        "https://example.org/page?f=a.diff",
        "https://example.org/page#node_modules",
        "https://example.org/docs/searching",
        "https://github.com/o/r/pull/1",
    ]

    def test_penalised_paths_flagged_in_every_mode(self):
        for mode in MODES:
            got = rows(self.PENALISED, mode=mode)
            for u in self.PENALISED:
                with self.subTest(mode=mode, url=u):
                    self.assertIn("non-content", got[u]["flags"])

    def test_clean_paths_not_flagged_in_every_mode(self):
        for mode in MODES:
            got = rows(self.CLEAN, mode=mode)
            for u in self.CLEAN:
                with self.subTest(mode=mode, url=u):
                    self.assertNotIn("non-content", got[u]["flags"])

    def test_penalty_is_28_points(self):
        a, b = "https://docs.python.org/search", "https://docs.python.org/3/library/os.html"
        got = rows([a, b], mode="official-docs")
        self.assertEqual(got[b]["score"], 88.0)
        self.assertEqual(got[b]["verdict"], "PRIMARY")
        self.assertEqual(got[a]["score"], 60.0)
        self.assertEqual(got[a]["verdict"], "SKIM")
        self.assertIn("non-content", got[a]["flags"])

    def test_flag_in_table_signals_column(self):
        p = subprocess.run([sys.executable, CLI, "--no-net", "-u", "https://example.org/search"],
                           capture_output=True, text=True, timeout=60)
        self.assertEqual(p.returncode, 0, p.stderr)
        line = [l for l in p.stdout.splitlines() if "https://example.org/search" in l][0]
        self.assertIn("non-content", line)


class RegistryStaleVersionTests(unittest.TestCase):
    VERSIONED = [
        "https://pypi.org/project/foo/0.7.5/",
        "https://www.pypi.org/project/foo/1.2/",
        "https://www.npmjs.com/package/foo/v/1.0.0",
        "https://npmjs.com/package/@scope/foo/v/2.3.4",
    ]
    UNVERSIONED = [
        "https://pypi.org/project/foo/",
        "https://www.pypi.org/project/foo/",
        "https://www.npmjs.com/package/foo",
        "https://www.npmjs.com/package/@scope/foo",
    ]

    def test_versioned_release_pages_flagged(self):
        for mode in ["official-docs", "non-academic"]:
            got = rows(self.VERSIONED, mode=mode)
            for u in self.VERSIONED:
                with self.subTest(mode=mode, url=u):
                    self.assertIn("stale-version", got[u]["flags"])

    def test_unversioned_pages_not_flagged(self):
        for mode in ["official-docs", "non-academic"]:
            got = rows(self.UNVERSIONED, mode=mode)
            for u in self.UNVERSIONED:
                with self.subTest(mode=mode, url=u):
                    self.assertNotIn("stale-version", got[u]["flags"])

    def test_versioned_drops_28_vs_unversioned(self):
        a, b = "https://pypi.org/project/foo/0.7.5/", "https://pypi.org/project/foo/"
        got = rows([a, b], mode="official-docs")
        self.assertAlmostEqual(got[b]["score"] - got[a]["score"], 28.0)

    def test_doc_version_exempts_matching_release_only(self):
        cur, other = "https://pypi.org/project/foo/0.7.5/", "https://pypi.org/project/foo/0.8.0/"
        got = rows([cur, other], "--doc-version", "pypi.org=0.7.5", mode="official-docs")
        self.assertNotIn("stale-version", got[cur]["flags"])
        self.assertIn("stale-version", got[other]["flags"])


class DocVariantTests(unittest.TestCase):
    APP = "https://nextjs.org/docs/app/building-your-application/routing"
    PAGES = "https://nextjs.org/docs/pages/building-your-application/routing"
    BLOG = "https://nextjs.org/blog/x"
    ALL = [APP, PAGES, BLOG]

    def test_no_option_no_variant_penalty(self):
        got = rows(self.ALL, mode="official-docs")
        for u in self.ALL:
            with self.subTest(url=u):
                self.assertNotIn("wrong-variant", got[u]["flags"])

    def test_app_variant_penalises_pages(self):
        got = rows(self.ALL, "--doc-variant", "nextjs.org=app", mode="official-docs")
        self.assertIn("wrong-variant", got[self.PAGES]["flags"])
        self.assertNotIn("wrong-variant", got[self.APP]["flags"])
        self.assertNotIn("wrong-variant", got[self.BLOG]["flags"])

    def test_pages_variant_penalises_app(self):
        got = rows(self.ALL, "--doc-variant", "nextjs.org=pages", mode="official-docs")
        self.assertIn("wrong-variant", got[self.APP]["flags"])
        self.assertNotIn("wrong-variant", got[self.PAGES]["flags"])
        self.assertNotIn("wrong-variant", got[self.BLOG]["flags"])

    def test_wrong_variant_costs_28(self):
        base = rows([self.PAGES], mode="official-docs")[self.PAGES]["score"]
        pen = rows([self.PAGES], "--doc-variant", "nextjs.org=app",
                   mode="official-docs")[self.PAGES]["score"]
        self.assertAlmostEqual(base - pen, 28.0)

    def test_variant_applies_in_other_modes(self):
        for mode in MODES:
            with self.subTest(mode=mode):
                self.assertIn("wrong-variant",
                              flags(self.PAGES, "--doc-variant", "nextjs.org=app", mode=mode))

    def test_unregistered_domain_exits_3(self):
        p = run([self.APP], "--doc-variant", "example.org=app")
        self.assertEqual(p.returncode, 3, p.stdout + p.stderr)
        self.assertTrue(p.stderr.strip())

    def test_unknown_variant_exits_3(self):
        p = run([self.APP], "--doc-variant", "nextjs.org=bogus")
        self.assertEqual(p.returncode, 3, p.stdout + p.stderr)
        self.assertTrue(p.stderr.strip())

    def test_malformed_spec_exits_2(self):
        p = run([self.APP], "--doc-variant", "nextjs.org")
        self.assertEqual(p.returncode, 2, p.stdout + p.stderr)
        self.assertTrue(p.stderr.strip())


if __name__ == "__main__":
    unittest.main()
