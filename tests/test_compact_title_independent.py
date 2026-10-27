"""Independent tests for the TITLE column of `srcscore.py --format compact` and what reads it.

Written from the change specification, not from the implementation:

1. render_compact: header `SCORE VERDICT T  SIGNALS                     URL  TITLE`; each PRIMARY/SUPPORT row is
   the old row line, then (only when there is a title) two spaces and the title with whitespace runs collapsed to
   one space and cut to io_format.COMPACT_TITLE_CHARS (40) characters. The title is the lookup's when there was
   one, else the input record's. Footer, `(no sources)`, the absent SKIM-and-below rows and the `--full-out`
   table (no title column) are unchanged.
2. arms.score_output / extract: score-run rows gain `title`, the stripped text after the URL, or "".
3. arms ingest: union.todo lists only main-context URLs (listed/exposed/read), never subagent-only ones.
4. SKILL.md Step 2 shows the column and forbids reading the URL files or a --full-out table.

The "same as before" row prefix is taken from render_table, whose output the change leaves alone.
No test needs the network or writes into the repo.
"""

import contextlib
import io
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from collections import Counter

import _pathsetup  # noqa: F401

import srcscore as S
from srcscore_core import io_format

ROOT = _pathsetup.ROOT
SRCSCORE = os.path.join(_pathsetup.SCRIPTS, "srcscore.py")
SKILL_MD = os.path.join(ROOT, "skills", "scored-web-search", "SKILL.md")
POLICY_EVAL = os.path.join(ROOT, "policy-eval")
if POLICY_EVAL not in sys.path:
    sys.path.insert(0, POLICY_EVAL)

import arms  # noqa: E402

VERDICTS = ("PRIMARY", "SUPPORT", "SKIM", "WEAK", "DROP", "BLOCKED")
HEADER = "SCORE VERDICT T  SIGNALS                     URL  TITLE"
OLD_HEADER = "SCORE VERDICT T  SIGNALS                     URL"


def cut(title):
    """The spec's title transform: collapse every whitespace run to one space, then keep 40 characters."""
    return " ".join(title.split())[:40]


def srow(url, verdict, score, tier="1", title="", signals=(), flags=(), fetch=None):
    """A scorer row in the shape `--format json` prints."""
    return {"url": url, "fetch_url": fetch or url, "title": title, "score": score, "verdict": verdict,
            "tier": tier, "matched": None, "signals": list(signals), "flags": list(flags), "meta": {}}


def table_lines(rows):
    """render_table's row lines by fetch address: the 'same as before' prefix of a compact row."""
    return io_format.render_table(rows).split("\n")[1:-1]


def counts_line(rows):
    c = Counter(r["verdict"] for r in rows)
    return "-- %d sources | " % len(rows) + " | ".join("%s %d" % (v, c[v]) for v in VERDICTS)


# --------------------------------------------------------------------------- 1a. render_compact

class TestRenderCompact(unittest.TestCase):

    def lines(self, rows):
        return io_format.render_compact(rows).split("\n")

    def test_title_chars_constant(self):
        self.assertEqual(io_format.COMPACT_TITLE_CHARS, 40)

    def test_header(self):
        self.assertEqual(self.lines([srow("https://p.org/a", "PRIMARY", 90.0, title="T")])[0], HEADER)

    def test_header_when_nothing_is_listed(self):
        rows = [srow("https://s.org/a", "SKIM", 50.0, title="skim title")]
        self.assertEqual(self.lines(rows), [HEADER, counts_line(rows)])

    def test_row_literal(self):
        r = srow("https://p.org/a", "PRIMARY", 91.0, tier="1", title="Attention Is All You Need",
                 signals=["cit=5"])
        want = "%5.1f %-8s %-2s %-27s %s" % (91.0, "PRIMARY", "1", "cit=5", "https://p.org/a")
        self.assertEqual(self.lines([r])[1], want + "  Attention Is All You Need")

    def test_row_is_old_line_plus_title(self):
        rows = [srow("https://p.org/a", "PRIMARY", 91.0, title="Alpha", signals=["cit=1.2k", "y2019"]),
                srow("https://s.org/b", "SUPPORT", 70.0, tier="3", title="Bravo page", flags=["net:off"]),
                srow("https://s.org/c?id=3", "SUPPORT", 63.0, tier="2", title="Charlie",
                     signals=["a" * 20], flags=["b" * 20])]
        got = self.lines(rows)[1:-1]
        self.assertEqual(got, [t + "  " + r["title"] for t, r in zip(table_lines(rows), rows)])

    def test_signals_still_cut_to_27(self):
        r = srow("https://p.org/a", "PRIMARY", 90.0, title="T", signals=["x" * 40])
        line = self.lines([r])[1]
        self.assertIn(" " + "x" * 27 + " https://p.org/a  T", line)
        self.assertNotIn("x" * 28, line)

    def test_fetch_address_is_the_url_column(self):
        r = srow("https://www.ncbi.nlm.nih.gov/pmc/articles/PMC1/", "PRIMARY", 88.0, title="PMC paper",
                 fetch="https://pmc.ncbi.nlm.nih.gov/articles/PMC1/")
        line = self.lines([r])[1]
        self.assertTrue(line.endswith(" https://pmc.ncbi.nlm.nih.gov/articles/PMC1/  PMC paper"), line)
        self.assertNotIn("www.ncbi.nlm.nih.gov", line)

    def test_whitespace_runs_collapse(self):
        cases = {
            "a\nb\tc\r\nd   e": "a b c d e",
            "  padded  title  ": "padded title",
            "tab\t\t\tand\n\n\nnewlines": "tab and newlines",
            "non breaking space": "non breaking space",
        }
        for title, want in cases.items():
            with self.subTest(title=title):
                line = self.lines([srow("https://p.org/a", "PRIMARY", 90.0, title=title)])[1]
                self.assertTrue(line.endswith("https://p.org/a  " + want), repr(line))

    def test_cut_to_40_characters(self):
        for title in ("x" * 39, "y" * 40, "z" * 41, "word " * 20,
                      "Attention Is All You Need: A Long Subtitle That Goes On"):
            with self.subTest(title=title):
                line = self.lines([srow("https://p.org/a", "PRIMARY", 90.0, title=title)])[1]
                self.assertEqual(line.split("https://p.org/a  ", 1)[1], cut(title))
        line = self.lines([srow("https://p.org/a", "PRIMARY", 90.0, title="z" * 41)])[1]
        self.assertTrue(line.endswith("  " + "z" * 40), line)

    def test_collapse_happens_before_the_cut(self):
        title = "a" + " \n\t " * 30 + "b" + " " * 50 + "c"
        line = self.lines([srow("https://p.org/a", "PRIMARY", 90.0, title=title)])[1]
        self.assertTrue(line.endswith("https://p.org/a  a b c"), repr(line))

    def test_cut_counts_characters_not_bytes(self):
        title = "日本語" * 20                     # 60 CJK characters
        line = self.lines([srow("https://p.org/a", "PRIMARY", 90.0, title=title)])[1]
        self.assertEqual(line.split("https://p.org/a  ", 1)[1], title[:40])

    def test_empty_title_has_no_separator(self):
        rows = [srow("https://p.org/a", "PRIMARY", 90.0, title=""),
                srow("https://s.org/b", "SUPPORT", 70.0, title="   \n\t ")]
        got = self.lines(rows)[1:-1]
        self.assertEqual(got, table_lines(rows))
        for line in got:
            self.assertTrue(line.endswith(("https://p.org/a", "https://s.org/b")), repr(line))

    def test_rows_below_support_absent_with_their_titles(self):
        rows = [srow("https://p.org/a", "PRIMARY", 90.0, title="Keep primary"),
                srow("https://s.org/b", "SUPPORT", 70.0, title="Keep support"),
                srow("https://k.org/c", "SKIM", 50.0, title="MARK-skim"),
                srow("https://w.org/d", "WEAK", 35.0, title="MARK-weak"),
                srow("https://d.org/e", "DROP", 10.0, title="MARK-drop"),
                srow("https://b.org/f", "BLOCKED", 0.0, tier="block", title="MARK-blocked")]
        out = io_format.render_compact(rows)
        self.assertEqual(len(out.split("\n")), 4)
        self.assertNotIn("MARK-", out)
        for u in ("k.org", "w.org", "d.org", "b.org"):
            self.assertNotIn(u, out)
        self.assertIn("Keep primary", out)
        self.assertIn("Keep support", out)

    def test_footer_unchanged(self):
        rows = [srow("https://p.org/a", "PRIMARY", 90.0, title="A"),
                srow("https://s.org/b", "SUPPORT", 70.0, title="B"),
                srow("https://k.org/c", "SKIM", 50.0, title="C"),
                srow("https://b.org/f", "BLOCKED", 0.0, tier="block", title="F")]
        self.assertEqual(self.lines(rows)[-1], counts_line(rows))
        self.assertEqual(counts_line(rows), "-- 4 sources | PRIMARY 1 | SUPPORT 1 | SKIM 1 | WEAK 0 | DROP 0 | BLOCKED 1")

    def test_no_sources(self):
        self.assertEqual(io_format.render_compact([]), "(no sources)")

    def test_table_has_no_title_column(self):
        rows = [srow("https://p.org/a", "PRIMARY", 90.0, title="Table title marker")]
        t = io_format.render_table(rows)
        self.assertEqual(t.split("\n")[0], OLD_HEADER)
        self.assertNotIn("Table title marker", t)
        self.assertNotIn("TITLE", t)


# --------------------------------------------------------------------------- 1b. which title

class TestTitleSource(unittest.TestCase):
    URL = "https://www.nature.com/articles/s41586-020-2649-2"

    def score(self, title, injected=None):
        return S.score_one({"url": self.URL, "title": title}, S.load_policy(), S.NullCache(), "ai", False,
                           injected)

    def sch(self, **kw):
        return {"scholar": {"citations": 500, "year": 2020, "age_years": 5.0, "peer_reviewed": True, **kw}}

    def compact_title(self, row):
        self.assertIn(row["verdict"], ("PRIMARY", "SUPPORT"), row)
        line = io_format.render_compact([row]).split("\n")[1]
        return line.split(row["fetch_url"] + "  ", 1)[1] if (row["fetch_url"] + "  ") in line else ""

    def test_lookup_title_wins(self):
        row = self.score("Record title", self.sch(title="Lookup\n  title"))
        self.assertEqual(row["title"], "Lookup\n  title")
        self.assertEqual(self.compact_title(row), "Lookup title")

    def test_record_title_without_lookup(self):
        row = self.score("Record\ttitle")
        self.assertEqual(self.compact_title(row), "Record title")

    def test_record_title_when_lookup_has_none(self):
        row = self.score("Record title", self.sch())
        self.assertEqual(self.compact_title(row), "Record title")

    def test_no_title_anywhere(self):
        row = self.score("")
        line = io_format.render_compact([row]).split("\n")[1]
        self.assertTrue(line.endswith(row["fetch_url"]), repr(line))


# --------------------------------------------------------------------------- 1c. the CLI

def run(*args):
    p = subprocess.run([sys.executable, SRCSCORE, "--no-net", *args], capture_output=True, text=True)
    if p.returncode != 0:
        raise AssertionError("srcscore exit %d: %s" % (p.returncode, p.stderr))
    return p.stdout


NATURE = "https://www.nature.com/articles/s41586-020-2649-2"
OLD_PMC = "https://www.ncbi.nlm.nih.gov/pmc/articles/PMC1234567/"
DOI = "https://doi.org/10.1038/s41586-020-2649-2"
RECORDS = [
    {"url": NATURE, "title": "Array programming\nwith\tNumPy   and a subtitle long enough to be cut"},
    {"url": OLD_PMC, "title": ""},
    {"url": DOI, "title": "Short DOI title"},
    {"url": "https://arxiv.org/abs/1706.03762", "title": "MARK skim title"},
    {"url": "https://en.wikipedia.org/wiki/Transformer", "title": "MARK weak title"},
    {"url": "https://www.scribd.com/document/1/x", "title": "MARK blocked title"},
]


class TestCli(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp(prefix="compact-title-")
        cls.inp = os.path.join(cls.tmp, "urls-1.json")
        with open(cls.inp, "w") as fh:
            json.dump(RECORDS, fh)
        base = ("--mode", "academic", "--in", cls.inp)
        cls.rows = json.loads(run(*base, "--format", "json"))
        cls.table = run(*base, "--format", "table")
        cls.full = os.path.join(cls.tmp, "full-1.txt")
        cls.out = run(*base, "--format", "compact", "--full-out", cls.full)
        cls.plain = run(*base, "--format", "compact")

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def test_fixture_has_listed_and_unlisted_rows(self):
        v = {r["url"]: r["verdict"] for r in self.rows}
        for u in (NATURE, OLD_PMC, DOI):
            self.assertIn(v[u], ("PRIMARY", "SUPPORT"), u)
        self.assertTrue(any(x not in ("PRIMARY", "SUPPORT") for x in v.values()))

    def test_rows_are_table_lines_plus_cut_title(self):
        lines = self.out.rstrip("\n").split("\n")
        self.assertEqual(lines[0], HEADER)
        by_addr = {l.split()[-1]: l for l in self.table.rstrip("\n").split("\n")[1:-1]}
        want = []
        for r in self.rows:
            if r["verdict"] in ("PRIMARY", "SUPPORT"):
                t = cut(r["title"])
                want.append(by_addr[r["fetch_url"]] + ("  " + t if t else ""))
        self.assertEqual(lines[1:-1], want)

    def test_titles_seen(self):
        self.assertIn("  " + cut(RECORDS[0]["title"]), self.out)
        self.assertEqual(len(cut(RECORDS[0]["title"])), 40)
        self.assertIn("https://pmc.ncbi.nlm.nih.gov/articles/PMC1234567/\n", self.out)
        self.assertIn("  Short DOI title", self.out)

    def test_below_support_absent(self):
        self.assertNotIn("MARK", self.out)
        for r in self.rows:
            if r["verdict"] not in ("PRIMARY", "SUPPORT"):
                self.assertNotIn(r["fetch_url"], self.out)

    def test_footer(self):
        self.assertEqual(self.out.rstrip("\n").split("\n")[-1], counts_line(self.rows))

    def test_full_out_is_the_table_without_titles(self):
        with open(self.full) as fh:
            full = fh.read()
        self.assertEqual(full.strip(), self.table.strip())
        self.assertEqual(full.split("\n")[0], OLD_HEADER)
        for rec in RECORDS:
            if rec["title"]:
                self.assertNotIn(cut(rec["title"]), full)
        self.assertEqual(self.out, self.plain)

    def test_url_flag_rows_have_no_title(self):
        out = run("--mode", "academic", "--format", "compact", "-u", NATURE)
        lines = out.rstrip("\n").split("\n")
        self.assertEqual(lines[0], HEADER)
        self.assertEqual(len(lines), 3)
        self.assertTrue(lines[1].endswith(NATURE), repr(lines[1]))

    def test_arms_reads_back_the_cli_output(self):
        got = arms.score_output(self.out, False)["rows"]
        want = [(r["fetch_url"], cut(r["title"])) for r in self.rows if r["verdict"] in ("PRIMARY", "SUPPORT")]
        self.assertEqual([(g["url"], g["title"]) for g in got], want)


# --------------------------------------------------------------------------- 2. arms parses the title

def line(score, verdict, tier, signals, url, title=None):
    base = "%5.1f %-8s %-2s %-27s %s" % (score, verdict, tier, signals, url)
    return base if title is None else base + "  " + title


def table(*rows):
    return "\n".join([HEADER, *rows, "-- 9 sources | PRIMARY 1 | SUPPORT 2 | SKIM 6 | WEAK 0 | DROP 0 | BLOCKED 0"])


class TestScoreOutputTitle(unittest.TestCase):

    def parse(self, *rows):
        return arms.score_output(table(*rows), False)["rows"]

    def test_title_parsed(self):
        (r,) = self.parse(line(98.0, "PRIMARY", "3", "cit=132.0k,y2017", "https://arxiv.org/abs/1706.03762",
                               "Attention Is All You Need"))
        self.assertEqual(r, {"url": "https://arxiv.org/abs/1706.03762", "score": 98.0, "verdict": "PRIMARY",
                             "tier": "3", "signals": "cit=132.0k,y2017", "title": "Attention Is All You Need"})

    def test_with_and_without_title_agree_on_other_fields(self):
        cases = [(91.0, "PRIMARY", "1", "cites:120,recent", "https://p.org/one"),
                 (100.0, "PRIMARY", "1", "a" * 27, "https://p.org/full"),
                 (64.5, "SUPPORT", "3", "", "https://s.org/two"),
                 (5.0, "SUPPORT", "0", "x", "https://s.org/low?id=3")]
        for c in cases:
            with self.subTest(url=c[-1]):
                (plain,) = self.parse(line(*c))
                (titled,) = self.parse(line(*c, title="A title: with words, 2026"))
                self.assertEqual(plain["title"], "")
                self.assertEqual(titled["title"], "A title: with words, 2026")
                self.assertEqual({k: v for k, v in titled.items() if k != "title"},
                                 {k: v for k, v in plain.items() if k != "title"})
                self.assertEqual(titled["url"], c[-1])

    def test_title_stripped(self):
        (r,) = self.parse(line(70.0, "SUPPORT", "2", "net:off", "https://s.org/a", "Cut at forty ") + "  \t")
        self.assertEqual(r["title"], "Cut at forty")
        self.assertEqual(r["url"], "https://s.org/a")

    def test_title_with_a_url_inside(self):
        (r,) = self.parse(line(70.0, "SUPPORT", "2", "net:off", "https://s.org/a", "See https://t.org/b now"))
        self.assertEqual(r["url"], "https://s.org/a")
        self.assertEqual(r["title"], "See https://t.org/b now")

    def test_title_that_is_a_url_after_signals(self):
        (r,) = self.parse(line(70.0, "SUPPORT", "2", "net:off", "https://s.org/a", "https://t.org/b"))
        self.assertEqual((r["url"], r["signals"], r["title"]), ("https://s.org/a", "net:off", "https://t.org/b"))

    def test_title_that_starts_with_a_url_after_empty_signals(self):
        """OPEN GAP. With an empty SIGNALS column, a title that begins with a URL (a search result whose title
        is its own address) is read as the URL and the real URL lands in `signals`. Spec 2: the URL is still
        the row's url and the title is the text after it."""
        (r,) = self.parse(line(64.5, "SUPPORT", "3", "", "https://s.org/two", "https://s.org/other page"))
        self.assertEqual((r["url"], r["signals"], r["title"]), ("https://s.org/two", "", "https://s.org/other page"))

    def test_mixed_rows(self):
        rows = self.parse(line(90.0, "PRIMARY", "1", "cit=5", "https://p.org/a", "Alpha"),
                          line(70.0, "SUPPORT", "2", "", "https://s.org/b"),
                          line(65.0, "SUPPORT", "3", "net:off", "https://s.org/c?x=1&y=2", "Charlie  page"))
        self.assertEqual([(r["url"], r["title"]) for r in rows],
                         [("https://p.org/a", "Alpha"), ("https://s.org/b", ""),
                          ("https://s.org/c?x=1&y=2", "Charlie  page")])

    def test_round_trip_through_render_compact(self):
        src = [srow("https://p.org/a", "PRIMARY", 91.0, title="Alpha\n\tbeta " * 6, signals=["cit=1"]),
               srow("https://www.ncbi.nlm.nih.gov/pmc/articles/PMC9/", "SUPPORT", 70.0, tier="2", title="",
                    fetch="https://pmc.ncbi.nlm.nih.gov/articles/PMC9/", flags=["net:off"]),
               srow("https://s.org/c", "SUPPORT", 66.0, tier="3", title="x" * 60),
               srow("https://k.org/d", "SKIM", 50.0, title="skim")]
        got = arms.score_output(io_format.render_compact(src), False)["rows"]
        want = [(r["fetch_url"], r["score"], r["verdict"], r["tier"], cut(r["title"]).strip())
                for r in src if r["verdict"] in ("PRIMARY", "SUPPORT")]
        self.assertEqual([(g["url"], g["score"], g["verdict"], g["tier"], g["title"]) for g in got], want)


# --------------------------------------------------------------------------- 2b. through extract

class Trace:
    def __init__(self, session="sess-title-0001"):
        self.L = [{"type": "system", "subtype": "init", "session_id": session}]
        self.n = 0

    def _id(self, p):
        self.n += 1
        return "%s%d" % (p, self.n)

    def call(self, name, inp, content, parent=None, tur=None):
        tid = self._id("tu")
        self.L.append({"type": "assistant", "parent_tool_use_id": parent,
                       "message": {"id": self._id("m"), "model": "claude-opus-5-5",
                                   "content": [{"type": "tool_use", "id": tid, "name": name, "input": inp}],
                                   "usage": {"input_tokens": 1, "output_tokens": 1}}})
        d = {"type": "user", "parent_tool_use_id": parent,
             "message": {"content": [{"type": "tool_result", "tool_use_id": tid, "content": content}]}}
        if tur is not None:
            d["tool_use_result"] = tur
        self.L.append(d)
        return tid

    def score(self, out, parent=None):
        return self.call("Bash", {"command": "python3 /x/skills/scored-web-search/scripts/srcscore.py "
                                             "--in /tmp/ssw/urls-1.json --mode academic --format compact"},
                         out, parent)

    def fetch(self, url, parent=None):
        return self.call("WebFetch", {"url": url, "prompt": "p"}, "page text", parent,
                         tur={"code": 200, "bytes": 9})

    def search(self, urls, summary="", parent=None):
        links = [{"title": "T " + u, "url": u} for u in urls]
        return self.call("WebSearch", {"query": "q"}, 'Web search results for query: "q"\n\nLinks: '
                         + json.dumps(links) + ("\n\n" + summary if summary else ""), parent)

    def subagent(self, body):
        tid = self._id("ag")
        self.L.append({"type": "assistant", "parent_tool_use_id": None,
                       "message": {"id": self._id("m"), "model": "claude-opus-5-5",
                                   "content": [{"type": "tool_use", "id": tid, "name": "Agent",
                                                "input": {"subagent_type": "general-purpose", "prompt": "go"}}],
                                   "usage": {"input_tokens": 1, "output_tokens": 1}}})
        self.L.append({"type": "system", "subtype": "task_started", "tool_use_id": tid,
                       "subagent_type": "general-purpose", "description": "d"})
        body(tid)
        self.L.append({"type": "user", "parent_tool_use_id": None,
                       "message": {"content": [{"type": "tool_result", "tool_use_id": tid,
                                                "content": [{"type": "text", "text": "done"}]}]},
                       "tool_use_result": {"agentId": "a" + tid, "agentType": "general-purpose",
                                           "status": "completed"}})

    def done(self):
        self.L.append({"type": "result", "subtype": "success", "result": "REPORT", "num_turns": 3,
                       "total_cost_usd": 0.01, "modelUsage": {}})

    def write(self, path):
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w") as fh:
            for d in self.L:
                fh.write(json.dumps(d) + "\n")
        return path


class TestExtractTitle(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="compact-title-trace-")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_score_run_rows_carry_title(self):
        t = Trace()
        t.score(table(line(90.0, "PRIMARY", "1", "cit=5", "https://p.org/a", "Alpha paper"),
                      line(70.0, "SUPPORT", "2", "net:off", "https://s.org/b")))
        t.done()
        ev = arms.extract(t.write(os.path.join(self.tmp, "run", "trace", "trace.jsonl")))
        rows = ev["score_runs"][0]["rows"]
        self.assertEqual([(r["url"], r["title"]) for r in rows],
                         [("https://p.org/a", "Alpha paper"), ("https://s.org/b", "")])
        lvl = arms.arm_urls(ev)
        self.assertEqual(lvl.get(arms.norm("https://p.org/a")), "listed")
        self.assertEqual(lvl.get(arms.norm("https://s.org/b")), "listed")


# --------------------------------------------------------------------------- 3. ingest: main context only

ROW_LISTED = "https://listed.org/score-row"
MAIN_READ = "https://read.org/main-fetch"
CROSS = "https://cross.org/sub-read-here-listed-there"
EXPOSED = "https://exposed.org/main-search"
SUB_ONLY_READ = "https://subread.org/only"
SUB_ONLY_SEEN = "https://subseen.org/only"
BOTH_SUB = "https://bothsub.org/seen-in-both-arms"


def with_arm():
    t = Trace("sess-with")
    t.score(table(line(90.0, "PRIMARY", "1", "cit=5", ROW_LISTED, "Listed title")))
    t.fetch(MAIN_READ)

    def body(tid):
        t.search([SUB_ONLY_SEEN, BOTH_SUB], parent=tid)
        t.fetch(CROSS, parent=tid)
        t.fetch(SUB_ONLY_READ, parent=tid)
    t.subagent(body)
    t.done()
    return t


def without_arm():
    t = Trace("sess-without")
    t.search([CROSS])                                   # listed: links, no summary
    t.search([EXPOSED], summary="a summary of results")

    def body(tid):
        t.search([BOTH_SUB], parent=tid)
    t.subagent(body)
    t.done()
    return t


class TestIngestMainContextOnly(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="compact-title-runs-")
        self._saved = arms.RUNS
        arms.RUNS = type(arms.RUNS)(os.path.join(self.tmp, "runs"))
        os.makedirs(str(arms.RUNS))
        w = with_arm().write(os.path.join(self.tmp, "w", "trace", "trace.jsonl"))
        o = without_arm().write(os.path.join(self.tmp, "o", "trace", "trace.jsonl"))
        self.result = os.path.join(self.tmp, "result.json")
        with open(self.result, "w") as fh:
            json.dump({"cases": [{"name": "ct1", "arms": {
                "with": [{"tracePath": w, "error": None}], "without": [{"tracePath": o, "error": None}]}}]}, fh)

    def tearDown(self):
        arms.RUNS = self._saved
        shutil.rmtree(self.tmp, ignore_errors=True)

    def ingest(self):
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            arms.cmd_ingest(self.result)
        with open(os.path.join(str(arms.RUNS), "ct1.union.todo.json")) as fh:
            return json.load(fh)

    def test_levels_are_what_the_fixture_means(self):
        self.ingest()
        ev = {}
        for arm in ("with", "without"):
            with open(os.path.join(str(arms.RUNS), "ct1.%s.events.json" % arm)) as fh:
                ev[arm] = arms.arm_urls(json.load(fh))
        n = arms.norm
        self.assertEqual(ev["with"], {n(ROW_LISTED): "listed", n(MAIN_READ): "read", n(CROSS): "sub_read",
                                      n(SUB_ONLY_READ): "sub_read", n(SUB_ONLY_SEEN): "sub_seen",
                                      n(BOTH_SUB): "sub_seen"})
        self.assertEqual(ev["without"], {n(CROSS): "listed", n(EXPOSED): "exposed", n(BOTH_SUB): "sub_seen"})

    def test_without_judge_file(self):
        todo = self.ingest()
        n = arms.norm
        self.assertEqual({n(i["url"]) for i in todo}, {n(ROW_LISTED), n(MAIN_READ), n(CROSS), n(EXPOSED)})
        self.assertEqual(len(todo), 4, "no duplicates")
        for i in todo:
            self.assertEqual(sorted(i), ["title", "url"])

    def test_judged_urls_left_out(self):
        with open(os.path.join(str(arms.RUNS), "ct1.judge.json"), "w") as fh:
            json.dump({"id": "ct1", "judged": [{"url": "https://www.listed.org/score-row/", "judgment": "useful"},
                                               {"url": SUB_ONLY_READ, "judgment": "junk"}]}, fh)
        todo = self.ingest()
        n = arms.norm
        self.assertEqual({n(i["url"]) for i in todo}, {n(MAIN_READ), n(CROSS), n(EXPOSED)})

    def test_hash_order(self):
        import hashlib
        urls = [i["url"] for i in self.ingest()]
        self.assertEqual(urls, sorted(urls, key=lambda u: hashlib.sha1(u.encode()).hexdigest()))


# --------------------------------------------------------------------------- 4. SKILL.md Step 2

class TestSkillStep2(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        with open(SKILL_MD, encoding="utf-8") as fh:
            text = fh.read()
        m = re.search(r"^### Step 2 (.*?)(?=^###)", text, re.S | re.M)
        assert m, "no ### Step 2 section"
        cls.step = m.group(1)
        blocks = re.findall(r"```[^\n]*\n(.*?)```", cls.step, re.S)
        cls.example = next((b for b in blocks if "SCORE VERDICT" in b), "")
        cls.prose = re.sub(r"```.*?```", " ", cls.step, flags=re.S)
        cls.sentences = [s for s in re.split(r"(?<=[.!?])\s+", " ".join(cls.prose.split()))]

    def test_example_header_has_title_column(self):
        self.assertEqual(self.example.split("\n")[0], HEADER)

    def test_example_rows_carry_titles(self):
        body = [l for l in self.example.split("\n") if re.match(r"^\s*\d+\.\d\s+(PRIMARY|SUPPORT)\b", l)]
        self.assertTrue(body, self.example)
        rows = arms.score_output(self.example, False)["rows"]
        self.assertEqual(len(rows), len(body))
        for r in rows:
            with self.subTest(url=r["url"]):
                self.assertTrue(r["title"], r)
                self.assertLessEqual(len(r["title"]), 40)

    def test_rows_are_all_it_chooses_from(self):
        hits = [s for s in self.sentences
                if re.search(r"(?i)\bchoose\b|\bchoice\b|\bpick\b", s)
                and re.search(r"(?i)\b(all|every\w*|only)\b", s) and re.search(r"(?i)\brows?\b", s)]
        self.assertTrue(hits, self.prose)

    def test_forbids_reading_url_files_and_full_out(self):
        s = " ".join(self.prose.split())
        m = re.search(r"(?i)[^.]*\bURL files?\b[^.]*--full-out[^.]*\.[^.]*\.", s) \
            or re.search(r"(?i)[^.]*--full-out[^.]*\bURL files?\b[^.]*\.[^.]*\.", s)
        self.assertTrue(m, s)
        rule = m.group(0)
        self.assertRegex(rule, r"(?i)\b(do not|don't|never)\b")
        for verb in ("open", "print", "grep"):
            self.assertRegex(rule, r"(?i)\b%s\b" % verb)
        self.assertRegex(rule, r"(?i)\btitles?\b")
        self.assertRegex(rule, r"(?i)below SUPPORT")


if __name__ == "__main__":
    unittest.main()
