"""Independent tests for issue 4: fetch_url normalisation, repeatable --in, --format compact,
--full-out, and the SKILL.md wording. Written from the issue spec."""

import json
import os
import re
import subprocess
import sys
import tempfile
import unittest
from collections import Counter

import _pathsetup  # noqa: F401
from srcscore_core.identifiers import fetch_url

ROOT = _pathsetup.ROOT
SRCSCORE = os.path.join(_pathsetup.SCRIPTS, "srcscore.py")
SKILL_MD = os.path.join(ROOT, "skills", "scored-web-search", "SKILL.md")
POLICY_EVAL = os.path.join(ROOT, "policy-eval")
if POLICY_EVAL not in sys.path:
    sys.path.insert(0, POLICY_EVAL)
import arms  # noqa: E402

VERDICTS = ("PRIMARY", "SUPPORT", "SKIM", "WEAK", "DROP", "BLOCKED")
OLD_PMC = "https://www.ncbi.nlm.nih.gov/pmc/articles/PMC1234567/"
NEW_PMC = "https://pmc.ncbi.nlm.nih.gov/articles/PMC1234567/"
ARXIV_NEW = "https://arxiv.org/abs/2401.01234v2"
ARXIV_OLD = "https://arxiv.org/abs/1706.03762"
BLOCKED_URL = "https://www.scribd.com/document/1/x"


def run(*args, stdin=None):
    return subprocess.run([sys.executable, SRCSCORE, "--no-net", *args],
                          capture_output=True, text=True, input=stdin)


def ok(p):
    if p.returncode != 0:
        raise AssertionError("srcscore exit %d: %s" % (p.returncode, p.stderr))
    return p.stdout


# --------------------------------------------------------------------------- 1. fetch_url

class FetchUrlPmc(unittest.TestCase):
    def test_variants(self):
        cases = {
            "https://www.ncbi.nlm.nih.gov/pmc/articles/PMC123/": "https://pmc.ncbi.nlm.nih.gov/articles/PMC123/",
            "http://www.ncbi.nlm.nih.gov/pmc/articles/PMC123/": "https://pmc.ncbi.nlm.nih.gov/articles/PMC123/",
            "https://ncbi.nlm.nih.gov/pmc/articles/PMC123": "https://pmc.ncbi.nlm.nih.gov/articles/PMC123",
            "https://www.ncbi.nlm.nih.gov/pmc/articles/PMC999/figure/F1/?report=objectonly":
                "https://pmc.ncbi.nlm.nih.gov/articles/PMC999/figure/F1/?report=objectonly",
        }
        for src, want in cases.items():
            self.assertEqual(fetch_url(src), want, src)

    def test_non_matching_unchanged(self):
        for u in ["https://pmc.ncbi.nlm.nih.gov/articles/PMC123/",
                  "https://www.ncbi.nlm.nih.gov/pubmed/12345",
                  "https://www.ncbi.nlm.nih.gov/pmc/",
                  "https://www.ncbi.nlm.nih.gov/pmc/journals/123/",
                  "https://example.org/pmc/articles/PMC123/"]:
            self.assertEqual(fetch_url(u), u, u)


class FetchUrlArxiv(unittest.TestCase):
    def test_boundary_2311_vs_2312(self):
        self.assertEqual(fetch_url("https://arxiv.org/abs/2311.99999"), "https://arxiv.org/abs/2311.99999")
        self.assertEqual(fetch_url("https://arxiv.org/abs/2312.00001"), "https://arxiv.org/html/2312.00001")

    def test_later_years(self):
        self.assertEqual(fetch_url("https://arxiv.org/abs/2401.01234"), "https://arxiv.org/html/2401.01234")
        self.assertEqual(fetch_url("https://arxiv.org/abs/2510.12345"), "https://arxiv.org/html/2510.12345")

    def test_version_kept(self):
        self.assertEqual(fetch_url("https://arxiv.org/abs/2401.01234v3"), "https://arxiv.org/html/2401.01234v3")

    def test_pdf_forms(self):
        self.assertEqual(fetch_url("https://arxiv.org/pdf/2401.01234"), "https://arxiv.org/html/2401.01234")
        self.assertEqual(fetch_url("https://arxiv.org/pdf/2401.01234.pdf"), "https://arxiv.org/html/2401.01234")
        self.assertEqual(fetch_url("https://arxiv.org/pdf/2401.01234v2.pdf"), "https://arxiv.org/html/2401.01234v2")

    def test_www_and_http(self):
        self.assertEqual(fetch_url("https://www.arxiv.org/abs/2401.01234"), "https://arxiv.org/html/2401.01234")
        self.assertEqual(fetch_url("http://arxiv.org/abs/2401.01234"), "https://arxiv.org/html/2401.01234")

    def test_query_string(self):
        self.assertEqual(fetch_url("https://arxiv.org/abs/2401.01234?context=cs"), "https://arxiv.org/html/2401.01234")

    def test_old_ids_unchanged(self):
        for u in ["https://arxiv.org/abs/1706.03762", "https://arxiv.org/pdf/1706.03762v5.pdf",
                  "https://arxiv.org/abs/2311.00001v2", "https://arxiv.org/abs/hep-th/9901001",
                  "https://arxiv.org/pdf/hep-th/9901001v1.pdf"]:
            self.assertEqual(fetch_url(u), u, u)

    def test_other_hosts_and_pages_unchanged(self):
        for u in ["https://export.arxiv.org/abs/2401.01234", "https://arxiv.org/html/2401.01234",
                  "https://arxiv.org/list/cs.CL/recent", "https://notarxiv.org/abs/2401.01234",
                  "https://example.com/", "not a url"]:
            self.assertEqual(fetch_url(u), u, u)


# --------------------------------------------------------------------------- 2. CLI

class JsonRowsCarryFetchUrl(unittest.TestCase):
    def test_url_kept_and_fetch_url_added(self):
        rows = json.loads(ok(run("--format", "json", "-u", OLD_PMC, "-u", ARXIV_NEW,
                                 "-u", ARXIV_OLD, "-u", "https://example.com/a")))
        by = {r["url"]: r for r in rows}
        self.assertEqual(set(by), {OLD_PMC, ARXIV_NEW, ARXIV_OLD, "https://example.com/a"})
        self.assertEqual(by[OLD_PMC]["fetch_url"], NEW_PMC)
        self.assertEqual(by[ARXIV_NEW]["fetch_url"], "https://arxiv.org/html/2401.01234v2")
        self.assertEqual(by[ARXIV_OLD]["fetch_url"], ARXIV_OLD)
        self.assertEqual(by["https://example.com/a"]["fetch_url"], "https://example.com/a")


class RenderedFormatsPrintFetchUrl(unittest.TestCase):
    def check(self, fmt):
        out = ok(run("--format", fmt, "-u", OLD_PMC, "-u", ARXIV_NEW, "-u", ARXIV_OLD))
        self.assertNotIn("www.ncbi.nlm.nih.gov/pmc", out, fmt)
        self.assertNotIn("arxiv.org/abs/2401", out, fmt)
        return out

    def test_table(self):
        out = self.check("table")
        self.assertIn(NEW_PMC, out)
        self.assertIn("https://arxiv.org/html/2401.01234v2", out)
        self.assertIn(ARXIV_OLD, out)

    def test_md(self):
        # The link text falls back to the title (or input URL); the link target is the fetch address.
        out = ok(run("--format", "md", "-u", OLD_PMC, "-u", ARXIV_NEW, "-u", ARXIV_OLD))
        targets = set(re.findall(r"\]\((https?://[^)]+)\)", out))
        self.assertEqual(targets, {NEW_PMC, "https://arxiv.org/html/2401.01234v2", ARXIV_OLD})

    def test_urls(self):
        out = self.check("urls")
        self.assertEqual(set(out.split()), {NEW_PMC, "https://arxiv.org/html/2401.01234v2", ARXIV_OLD})

    def test_compact(self):
        # Only PRIMARY/SUPPORT rows show; whatever URL lines exist must be fetch addresses.
        self.check("compact")


class MultipleIn(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp(prefix="issue4-")

    def write(self, name, text):
        p = os.path.join(self.d, name)
        with open(p, "w") as f:
            f.write(text)
        return p

    def test_concatenates_json_and_url_list(self):
        a = self.write("a.json", json.dumps([{"url": "https://a.example.org/1", "title": "A"},
                                             {"url": "https://a.example.org/2", "title": "B",
                                              "date": "2025-01-01"}]))
        b = self.write("b.txt", "https://b.example.org/1\n- https://b.example.org/2 | Title B2\n")
        c = self.write("c.json", json.dumps(["https://c.example.org/1"]))
        rows = json.loads(ok(run("--format", "json", "--in", a, "--in", b, "--in", c)))
        self.assertEqual(sorted(r["url"] for r in rows),
                         sorted(["https://a.example.org/1", "https://a.example.org/2",
                                 "https://b.example.org/1", "https://b.example.org/2",
                                 "https://c.example.org/1"]))

    def test_single_in_still_works(self):
        a = self.write("a.txt", "https://a.example.org/1\n")
        rows = json.loads(ok(run("--format", "json", "--in", a)))
        self.assertEqual([r["url"] for r in rows], ["https://a.example.org/1"])

    def test_stdin_when_no_in(self):
        rows = json.loads(ok(run("--format", "json", stdin="https://s.example.org/1\nhttps://s.example.org/2\n")))
        self.assertEqual(sorted(r["url"] for r in rows), ["https://s.example.org/1", "https://s.example.org/2"])


MIX = [ARXIV_OLD, "https://arxiv.org/abs/2401.01234", "https://www.nature.com/articles/s41586-020-2649-2",
       "https://en.wikipedia.org/wiki/Transformer", "https://medium.com/@x/post",
       "https://random-unknown-blog.example/post", BLOCKED_URL, "https://www.scribd.com/doc/2/y",
       "https://www.reddit.com/r/x/comments/abc/t/"]


class Compact(unittest.TestCase):
    def setUp(self):
        args = []
        for u in MIX:
            args += ["-u", u]
        self.args = args
        self.rows = json.loads(ok(run("--format", "json", *args)))
        self.out = ok(run("--format", "compact", *args))

    def test_blocked_present_in_fixture(self):
        self.assertTrue(any(r["verdict"] == "BLOCKED" for r in self.rows), [r["verdict"] for r in self.rows])

    def test_header_rows_footer(self):
        lines = self.out.rstrip("\n").split("\n")
        self.assertTrue(lines[0].startswith("SCORE VERDICT"), lines[0])
        body = lines[1:-1]
        keep = [r for r in self.rows if r["verdict"] in ("PRIMARY", "SUPPORT")]
        self.assertEqual(len(body), len(keep), self.out)
        for line in body:
            self.assertRegex(line, r"\b(PRIMARY|SUPPORT)\b")
        for v in ("SKIM", "WEAK", "DROP", "BLOCKED"):
            for line in body:
                self.assertNotIn(" %s " % v, line)
        c = Counter(r["verdict"] for r in self.rows)
        want = "-- %d sources | " % len(self.rows) + " | ".join("%s %d" % (v, c[v]) for v in VERDICTS)
        self.assertEqual(lines[-1], want)

    def test_dropped_urls_absent(self):
        for r in self.rows:
            if r["verdict"] not in ("PRIMARY", "SUPPORT"):
                self.assertNotIn(r["fetch_url"], self.out)

    def test_empty(self):
        p = run("--format", "compact", "--min", "1000", *self.args)
        self.assertEqual(ok(p).strip(), "(no sources)")

    def test_full_out_equals_table(self):
        d = tempfile.mkdtemp(prefix="issue4-")
        path = os.path.join(d, "full.txt")
        compact = ok(run("--format", "compact", "--full-out", path, *self.args))
        table = ok(run("--format", "table", *self.args))
        with open(path) as f:
            full = f.read()
        self.assertEqual(full.strip(), table.strip())
        self.assertEqual(compact, self.out)
        self.assertIn(BLOCKED_URL, full)


# --------------------------------------------------------------------------- 4. arms.py

class ArmsNorm(unittest.TestCase):
    def test_pmc_old_and_new_same_key(self):
        self.assertEqual(arms.norm(OLD_PMC), arms.norm(NEW_PMC))
        self.assertEqual(arms.norm("http://ncbi.nlm.nih.gov/pmc/articles/PMC1234567"), arms.norm(NEW_PMC))

    def test_arxiv_new_same_key_as_html(self):
        self.assertEqual(arms.norm("https://arxiv.org/abs/2401.01234"), arms.norm("https://arxiv.org/html/2401.01234"))

    def test_arxiv_old_unchanged(self):
        self.assertNotEqual(arms.norm(ARXIV_OLD), arms.norm("https://arxiv.org/html/1706.03762"))


# --------------------------------------------------------------------------- 5. SKILL.md

class SkillDoc(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        with open(SKILL_MD, encoding="utf-8") as f:
            text = f.read()
        cls.steps = {}
        for m in re.finditer(r"^### Step (\S+)(.*?)(?=^### |^## |\Z)", text, re.S | re.M):
            cls.steps[m.group(1)] = m.group(2)

    def test_step1_file_and_count(self):
        s = self.steps["1"]
        self.assertRegex(s, r"(?i)file")
        self.assertRegex(s, r"(?i)path")
        self.assertRegex(s, r"(?i)count")

    def test_step2_compact(self):
        self.assertIn("--format compact", self.steps["2"])

    def test_step3_offset_and_wording(self):
        s = self.steps["3"]
        self.assertIn("offset", s)
        self.assertIn("beyond what was read", s)
        self.assertRegex(s, r"never\W+\"?not reported")


if __name__ == "__main__":
    unittest.main()
