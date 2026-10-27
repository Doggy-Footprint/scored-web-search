"""Independent tests for the doi.org label join, the ingest todo and the per-round `Degraded:` checks in
policy-eval/arms.py.

Written from the specification (the task brief and skills/scored-web-search/SKILL.md Steps 2, 2.5 and 6), not
from the implementation. Under test:

1. aliased_labels and cmd_analyze: a label on a doi.org link's target counts for the link in every label-based
   metric; the link's own label wins; <cid>.judge.json wins over <cid>.union.judge.json.
2. cmd_ingest: <cid>.union.todo.json leaves out a target whose doi.org link <cid>.judge.json labelled; only those
   doi.org links are scored, with the case's mode and field.
3. degraded_check's `unjudged` agreement: usable rounds' UNJUDGED verdicts must be reported.
4. degraded_traced's `judge_unavailable`: per round of new SUPPORT URLs.

No test touches the network or the repo: arms.score_all and arms.case_list are stubbed and arms.RUNS points
at a temp dir.
"""

import contextlib
import io
import json
import os
import shutil
import sys
import tempfile
import unittest

import _pathsetup  # noqa: F401  (sys.path bootstrap for scripts/)

ROOT = _pathsetup.ROOT
POLICY_EVAL = os.path.join(ROOT, "policy-eval")
if POLICY_EVAL not in sys.path:
    sys.path.insert(0, POLICY_EVAL)

import arms  # noqa: E402

n = arms.norm
SEARCHER = "scored-web-search:searcher"
JUDGE = "general-purpose"
VERDICTS = ("PRIMARY", "SUPPORT", "SKIM", "WEAK", "DROP", "BLOCKED")


# --------------------------------------------------------------------------- trace helpers

def row(score, verdict, tier, url, title="A title"):
    """One `srcscore.py --format compact` line."""
    return "%5.1f %-8s %-2s %-27s %s  %s" % (score, verdict, tier, "y2024", url, title)


def compact(rows, counts):
    total = sum(counts.get(v, 0) for v in VERDICTS)
    return "\n".join(["SCORE VERDICT T  SIGNALS                     URL  TITLE"] + list(rows)
                     + ["-- %d sources | %s" % (total, " | ".join("%s %d" % (v, counts.get(v, 0)) for v in VERDICTS))])


def framed(reply, aid):
    body = "\n".join("  " + l for l in reply.split("\n"))
    return ("[Subagent hand-back] The text below is the final report of a subagent. The report follows:\n"
            + body + "\nagentId: %s\n<usage>subagent_tokens: 1</usage>" % aid)


class Trace:
    """Minimal stream-json trace builder; main-agent calls have parent None."""

    def __init__(self, session="sess-fix-1"):
        self.L = [{"type": "system", "subtype": "init", "session_id": session}]
        self.k = 0

    def _id(self, p):
        self.k += 1
        return "%s%d" % (p, self.k)

    def _msg(self, parent, content):
        self.L.append({"type": "assistant", "parent_tool_use_id": parent,
                       "message": {"id": self._id("m"), "model": "claude-opus-5-5", "content": content,
                                   "usage": {"input_tokens": 1, "output_tokens": 1}}})

    def call(self, name, inp, result, parent=None, tur=None):
        tid = self._id("tu")
        self._msg(parent, [{"type": "tool_use", "id": tid, "name": name, "input": inp}])
        d = {"type": "user", "parent_tool_use_id": parent,
             "message": {"content": [{"type": "tool_result", "tool_use_id": tid, "content": result}]}}
        if tur is not None:
            d["tool_use_result"] = tur
        self.L.append(d)
        return tid

    def search(self, urls, parent=None, summary=""):
        links = json.dumps([{"title": "T " + u, "url": u} for u in urls])
        self.call("WebSearch", {"query": "q"}, 'Web search results for query: "q"\n\nLinks: ' + links
                  + ("\n\n" + summary if summary else ""), parent)

    def score(self, table, path="/tmp/ssw/urls-1.json"):
        self.call("Bash", {"command": "python3 /x/scripts/srcscore.py --in %s --format compact" % path}, table)

    def fetch(self, url, parent=None):
        self.call("WebFetch", {"url": url, "prompt": "p"}, "page text", parent, tur={"code": 200, "bytes": 9})

    def write(self, path, urls, parent=None):
        self.call("Write", {"file_path": path, "content": json.dumps([{"url": u, "title": "T"} for u in urls])},
                  "File created successfully at: " + path, parent)

    def agent(self, subtype, reply, body=None):
        tid = self._id("ag")
        self._msg(None, [{"type": "tool_use", "id": tid, "name": "Agent",
                          "input": {"subagent_type": subtype, "prompt": "go"}}])
        self.L.append({"type": "system", "subtype": "task_started", "tool_use_id": tid,
                       "subagent_type": subtype, "description": "d"})
        if body:
            body(tid)
        self._msg(tid, [{"type": "text", "text": reply}])
        aid = "agent" + tid
        self.L.append({"type": "user", "parent_tool_use_id": None,
                       "message": {"content": [{"type": "tool_result", "tool_use_id": tid,
                                                "content": [{"type": "text", "text": framed(reply, aid)}]}]},
                       "tool_use_result": {"agentId": aid, "agentType": subtype, "status": "completed"}})
        return tid

    def report(self, text="Report.\nDegraded: none"):
        self.L.append({"type": "result", "subtype": "success", "result": text, "num_turns": 3,
                       "total_cost_usd": 0.01,
                       "modelUsage": {"claude-opus-5-5": {"costUSD": 0.01, "webSearchRequests": 0}}})

    def dump(self, path):
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w") as fh:
            for d in self.L:
                fh.write(json.dumps(d) + "\n")
        return path


class RunsDirCase(unittest.TestCase):
    """arms.RUNS in a temp dir; arms.case_list and arms.score_all stubbed (calls recorded)."""

    CASE = {"id": "dz1", "mode": "news", "field": "bio", "expansion": False, "q": "q?"}
    TARGETS = {}            # doi link -> the address the scorer gives it (fetch_url)

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="arms-fixes-")
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
        self.score_calls.append({"urls": urls, "mode": mode, "field": field})
        targets = {n(k): v for k, v in self.TARGETS.items()}
        out = {}
        for u in urls:
            k = n(u)
            out[k] = {"url": u, "fetch_url": targets.get(k, u), "tier": "1" if k in targets else "3",
                      "score": 70.0, "verdict": "SUPPORT", "flags": []}
        return out

    def runs(self, name):
        return os.path.join(str(arms.RUNS), name)

    def write_json(self, name, obj):
        with open(self.runs(name), "w") as fh:
            json.dump(obj, fh)

    def read_json(self, name):
        with open(self.runs(name)) as fh:
            return json.load(fh)

    def quiet(self, fn, *a):
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            return fn(*a)


def judged(*pairs):
    return {"judged": [{"url": u, "judgment": j} for u, j in pairs]}


# --------------------------------------------------------------------------- 1a. aliased_labels

DOI = "https://doi.org/10.1234/abc.5678"
TARGET = "https://pub.example.org/article/abc-5678"
PLAIN = "https://plain.example.net/paper/9"


class TestAliasedLabels(unittest.TestCase):

    def setUp(self):
        self.alias = {n(TARGET): n(DOI)}

    def test_target_label_counts_for_link(self):
        out = arms.aliased_labels({n(TARGET): {"judgment": "useful"}}, self.alias)
        self.assertEqual(out.get(n(DOI)), {"judgment": "useful"})

    def test_link_own_label_wins_in_either_order(self):
        own, tgt = {"judgment": "junk"}, {"judgment": "useful"}
        for labels in ({n(DOI): own, n(TARGET): tgt}, {n(TARGET): tgt, n(DOI): own}):
            with self.subTest(order=list(labels)):
                self.assertEqual(arms.aliased_labels(labels, self.alias)[n(DOI)], own)

    def test_unaliased_labels_pass_through(self):
        labels = {n(PLAIN): {"judgment": "marginal"}, n(DOI): {"judgment": "junk"}}
        out = arms.aliased_labels(labels, self.alias)
        self.assertEqual(out[n(PLAIN)], {"judgment": "marginal"})
        self.assertEqual(out[n(DOI)], {"judgment": "junk"})
        self.assertEqual(arms.aliased_labels(labels, {}), labels)

    def test_input_not_mutated(self):
        labels = {n(TARGET): {"judgment": "useful"}}
        arms.aliased_labels(labels, self.alias)
        self.assertEqual(labels, {n(TARGET): {"judgment": "useful"}})


# --------------------------------------------------------------------------- 1b. cmd_analyze end to end

def doi_run_trace():
    """A skill run: the searcher saw the doi.org link; the score table lists its target as SUPPORT; the judge
    says USE on the target; the main agent reads the target. A plain PRIMARY row is read too."""
    t = Trace()

    def searcher(tid):
        t.search([DOI, PLAIN], parent=tid)
        t.write("/tmp/ssw/urls-1.json", [DOI, PLAIN], parent=tid)
    t.agent(SEARCHER, "/tmp/ssw/urls-1.json 2", body=searcher)
    t.score(compact([row(90.0, "PRIMARY", "1", PLAIN), row(70.0, "SUPPORT", "2", TARGET)],
                    {"PRIMARY": 1, "SUPPORT": 1}))
    t.agent(JUDGE, TARGET + " | USE | states the figure", body=lambda tid: t.fetch(TARGET, parent=tid))
    t.fetch(TARGET)
    t.fetch(PLAIN)
    t.report("Report.\n2 sources collected → 2 passed → 0 judged-out → 2 read\nDegraded: none")
    return t


class TestAnalyzeDoiJoin(RunsDirCase):

    TARGETS = {DOI: TARGET}

    def setUp(self):
        super().setUp()
        path = doi_run_trace().dump(os.path.join(self.tmp, "w", "trace", "trace.jsonl"))
        ev = arms.extract(path)
        ev.update({"id": "dz1", "arm": "with"})
        ev.pop("report", None)
        self.write_json("dz1.with.events.json", ev)

    def analyze(self, judge=None, union=None):
        if judge is not None:
            self.write_json("dz1.judge.json", judged(*judge))
        if union is not None:
            self.write_json("dz1.union.judge.json", judged(*union))
        self.quiet(arms.cmd_analyze)
        return self.read_json("dz1.arms.json")["arms"]["with"]

    def contamination_labels(self, arm, level):
        return {lab: sum(c.values()) for lab, c in arm["contamination"][level].items() if sum(c.values())}

    def assertDoiLabel(self, arm, lab):
        """The doi.org link (read by its target) carries `lab` in every label-based metric. PLAIN is labelled
        useful in every fixture."""
        want = {"useful": 1}
        want[lab] = want.get(lab, 0) + 1
        self.assertEqual(self.contamination_labels(arm, "read"), want)
        self.assertEqual(arm["sources_read"]["main_by_label"], want)
        self.assertEqual({k: dict(v) for k, v in arm["judge_vs_blind"].items()}, {"USE": {lab: 1}})
        useful = {n(PLAIN)} | ({n(DOI)} if lab == "useful" else set())
        self.assertEqual(set(arm["useful_reached"]), useful)

    def test_fixture_reads_the_link_by_its_target(self):
        arm = self.analyze(judge=[(PLAIN, "useful")])
        self.assertEqual(arm["sources_read"]["main_ok"], 2)
        self.assertEqual(arm["sources_read"]["main_by_label"], {"useful": 1, "unlabeled": 1})
        self.assertEqual({k: dict(v) for k, v in arm["judge_vs_blind"].items()}, {"USE": {"unlabeled": 1}})
        self.assertEqual(self.score_calls[0]["mode"], "news")
        self.assertIn(n(DOI), {n(u) for u in self.score_calls[0]["urls"]})

    def test_union_label_on_target_counts_for_link(self):
        arm = self.analyze(judge=[(PLAIN, "useful")], union=[(TARGET, "useful")])
        self.assertDoiLabel(arm, "useful")
        self.assertNotIn("unlabeled", self.contamination_labels(arm, "read"))

    def test_judge_label_on_target_counts_for_link(self):
        arm = self.analyze(judge=[(PLAIN, "useful"), (TARGET, "marginal")])
        self.assertDoiLabel(arm, "marginal")

    def test_link_own_label_wins_over_target(self):
        for union in ([(DOI, "junk"), (TARGET, "useful")], [(TARGET, "useful"), (DOI, "junk")]):
            with self.subTest(union=union):
                arm = self.analyze(judge=[(PLAIN, "useful")], union=union)
                self.assertDoiLabel(arm, "junk")

    def test_link_own_label_wins_both_in_judge(self):
        arm = self.analyze(judge=[(PLAIN, "useful"), (TARGET, "useful"), (DOI, "junk")])
        self.assertDoiLabel(arm, "junk")

    def test_judge_wins_over_union_on_target(self):
        arm = self.analyze(judge=[(PLAIN, "useful"), (TARGET, "marginal")], union=[(TARGET, "junk")])
        self.assertDoiLabel(arm, "marginal")

    def test_judge_wins_over_union_on_link(self):
        arm = self.analyze(judge=[(PLAIN, "useful"), (DOI, "marginal")], union=[(DOI, "junk")])
        self.assertDoiLabel(arm, "marginal")

    def test_no_doi_alias_leaves_labels_alone(self):
        self.TARGETS = {}
        arm = self.analyze(judge=[(PLAIN, "useful")], union=[(DOI, "junk")])
        # Without an alias the target is its own URL: the doi link's label does not reach it.
        self.assertEqual(arm["sources_read"]["main_by_label"], {"useful": 1, "unlabeled": 1})


# --------------------------------------------------------------------------- 2. cmd_ingest todo

D1 = "https://doi.org/10.1000/labelled"
T1 = "https://journal-a.example.org/full/labelled"
D2 = "https://doi.org/10.1000/unlabelled"
T2 = "https://journal-b.example.org/full/unlabelled"
P = "https://plain.example.com/report"
X = "https://other.example.com/judged"


def ingest_trace():
    """Main context holds the two targets and P (score-table rows); the doi.org links were only seen by the
    searcher subagent."""
    t = Trace()

    def searcher(tid):
        t.search([D1, D2, P], parent=tid)
        t.write("/tmp/ssw/urls-1.json", [D1, D2, P], parent=tid)
    t.agent(SEARCHER, "/tmp/ssw/urls-1.json 3", body=searcher)
    t.score(compact([row(90.0, "PRIMARY", "1", P), row(70.0, "SUPPORT", "2", T1), row(68.0, "SUPPORT", "2", T2)],
                    {"PRIMARY": 1, "SUPPORT": 2}))
    t.fetch(T1)
    t.report()
    return t


class TestIngestDoiTodo(RunsDirCase):

    TARGETS = {D1: T1, D2: T2}

    def setUp(self):
        super().setUp()
        tp = ingest_trace().dump(os.path.join(self.tmp, "w", "trace", "trace.jsonl"))
        self.result = os.path.join(self.tmp, "result.json")
        with open(self.result, "w") as fh:
            json.dump({"cases": [{"name": "dz1", "arms": {"with": [{"tracePath": tp, "error": None}]}}]}, fh)

    def todo(self):
        self.quiet(arms.cmd_ingest, self.result)
        return {n(i["url"]) for i in self.read_json("dz1.union.todo.json")}

    def test_fixture_pool(self):
        self.assertEqual(self.todo(), {n(P), n(T1), n(T2)})
        self.assertEqual(self.score_calls, [], "no judge.json: nothing to score")

    def test_target_of_labelled_doi_link_left_out(self):
        self.write_json("dz1.judge.json", judged((D1, "useful"), (X, "junk")))
        self.assertEqual(self.todo(), {n(P), n(T2)})

    def test_only_labelled_doi_links_scored_with_case_mode_and_field(self):
        self.write_json("dz1.judge.json", judged((D1, "useful"), (X, "junk"), (P, "marginal")))
        todo = self.todo()
        self.assertEqual(todo, {n(T2)})
        self.assertEqual(len(self.score_calls), 1)
        c = self.score_calls[0]
        self.assertEqual({n(u) for u in c["urls"]}, {n(D1)})
        self.assertEqual((c["mode"], c["field"]), ("news", "bio"))

    def test_no_doi_links_in_judge_means_no_scoring(self):
        self.write_json("dz1.judge.json", judged((X, "junk"), (P, "useful")))
        self.assertEqual(self.todo(), {n(T1), n(T2)})
        self.assertEqual(self.score_calls, [])

    def test_unlabelled_doi_target_stays(self):
        self.write_json("dz1.judge.json", judged((D1, "useful")))
        self.assertIn(n(T2), self.todo())

    def test_union_labels_do_not_drop_targets(self):
        # Only judge.json counts as already labelled; the union labeller's own file is what the todo feeds.
        self.write_json("dz1.union.judge.json", judged((D1, "useful")))
        self.assertEqual(self.todo(), {n(P), n(T1), n(T2)})


# --------------------------------------------------------------------------- 3/4. degraded helpers

def sr(urls_verdicts, exit=0):
    """A score run from (url, verdict) pairs."""
    return {"role": "main", "exit": exit, "chars": 1, "counts": {}, "lookups_failed": None, "full_out": None,
            "rows": [{"url": u, "score": 60.0, "verdict": v, "tier": "2", "signals": "", "title": ""}
                     for u, v in urls_verdicts]}


def jv(*items):
    """A judge return from (url, kind) pairs; kind USE, SKIP, page or fetch-error (the last two UNJUDGED)."""
    vs = []
    for u, k in items:
        if k in ("USE", "SKIP"):
            vs.append({"url": u, "verdict": k, "reason": "r", "kind": None})
        else:
            vs.append({"url": u, "verdict": "UNJUDGED", "reason": k + ": r", "kind": k})
    return {"tool_use_id": "j", "chars": 1, "kind": "judge", "verdicts": vs}


def ev_of(score_runs=(), rets=(), degraded=None):
    return {"searches": [], "fetches": [], "url_files": [], "score_runs": list(score_runs),
            "agent_returns": list(rets), "closing": {"summary": None, "degraded": degraded}}


def reported(raw, **items):
    d = {"raw": raw, "none": raw == "none", "judge_unavailable": "judge-unavailable" in raw}
    d.update(items)
    return d


A, B, C, Dd, E = ("https://s.example.org/%s" % k for k in "abcde")


class TestJudgeUnavailableRounds(unittest.TestCase):

    def unavailable(self, runs, rets):
        return arms.degraded_traced(ev_of(runs, rets))["judge_unavailable"]

    def test_spec_example_new_support_url_without_judge(self):
        self.assertIs(self.unavailable([sr([(A, "SUPPORT")]), sr([(A, "SUPPORT"), (C, "SUPPORT")])],
                                       [jv((A, "USE"))]), True)

    def test_rescoring_seen_support_urls_is_no_new_round(self):
        self.assertIs(self.unavailable([sr([(A, "SUPPORT"), (B, "SUPPORT")]), sr([(A, "SUPPORT")]),
                                        sr([(B, "SUPPORT"), (A, "SUPPORT"), (P, "PRIMARY")])],
                                       [jv((A, "USE"))]), False)

    def test_judge_skipped_some_new_urls_is_available(self):
        self.assertIs(self.unavailable([sr([(A, "SUPPORT")]), sr([(C, "SUPPORT"), (Dd, "SUPPORT"), (A, "SUPPORT")])],
                                       [jv((A, "USE")), jv((C, "page"))]), False)

    def test_new_urls_judged_only_in_earlier_seen_set_do_not_count(self):
        # Round 2 adds C and D; the second judge reply only re-judges A.
        self.assertIs(self.unavailable([sr([(A, "SUPPORT")]), sr([(C, "SUPPORT"), (Dd, "SUPPORT")])],
                                       [jv((A, "USE")), jv((A, "SKIP"))]), True)

    def test_url_seen_earlier_below_support_is_new_support(self):
        self.assertIs(self.unavailable([sr([(A, "SUPPORT"), (B, "PRIMARY")]), sr([(B, "SUPPORT")])],
                                       [jv((A, "USE"))]), True)

    def test_failed_score_run_is_not_a_round(self):
        self.assertIs(self.unavailable([sr([(A, "SUPPORT")]), sr([(C, "SUPPORT")], exit=4)],
                                       [jv((A, "USE"))]), False)

    def test_first_round_unjudged(self):
        self.assertIs(self.unavailable([sr([(A, "SUPPORT")]), sr([(A, "SUPPORT"), (C, "SUPPORT")])],
                                       [jv((C, "USE"))]), True)

    def test_new_url_judged_only_by_all_fetch_error_reply(self):
        self.assertIs(self.unavailable([sr([(A, "SUPPORT")]), sr([(C, "SUPPORT")])],
                                       [jv((A, "USE")), jv((C, "fetch-error"))]), True)

    def test_any_all_fetch_error_reply_is_unavailable(self):
        self.assertIs(self.unavailable([sr([(A, "SUPPORT"), (B, "SUPPORT")])],
                                       [jv((A, "USE"), (B, "SKIP")), jv((A, "fetch-error"), (B, "fetch-error"))]),
                      True)

    def test_partly_fetch_error_reply_is_usable(self):
        self.assertIs(self.unavailable([sr([(A, "SUPPORT")]), sr([(C, "SUPPORT"), (Dd, "SUPPORT")])],
                                       [jv((A, "USE")), jv((C, "fetch-error"), (Dd, "page"))]), False)

    def test_no_support_rows_needs_no_judge(self):
        self.assertIs(self.unavailable([sr([(P, "PRIMARY")]), sr([(P, "PRIMARY"), (E, "PRIMARY")])], []), False)

    def test_spec_example_via_trace(self):
        t = Trace()
        t.score(compact([row(70.0, "SUPPORT", "2", A)], {"SUPPORT": 1}))
        t.agent(JUDGE, A + " | USE | direct")
        t.score(compact([row(70.0, "SUPPORT", "2", A), row(69.0, "SUPPORT", "2", C)], {"SUPPORT": 2}),
                path="/tmp/ssw/urls-2.json")
        t.report("Report.\nDegraded: judge-unavailable")
        tmp = tempfile.mkdtemp(prefix="arms-fixes-")
        try:
            ev = arms.extract(t.dump(os.path.join(tmp, "trace", "trace.jsonl")))
        finally:
            shutil.rmtree(tmp, ignore_errors=True)
        self.assertIs(arms.degraded_traced(ev)["judge_unavailable"], True)
        self.assertIs(arms.degraded_check(ev)["agree"]["judge_unavailable"], True)


class TestUnjudgedAgreement(unittest.TestCase):

    def two_rounds(self, degraded):
        """Round 1's judge worked and left B page-UNJUDGED; round 2 added C and had no judge."""
        return ev_of([sr([(A, "SUPPORT"), (B, "SUPPORT")]), sr([(A, "SUPPORT"), (B, "SUPPORT"), (C, "SUPPORT")])],
                     [jv((A, "USE"), (B, "page"))], degraded)

    def test_traced_counts_usable_round(self):
        d = arms.degraded_traced(self.two_rounds(None))
        self.assertIs(d["judge_unavailable"], True)
        self.assertEqual(d["unjudged"], [1, 0, 1])

    def test_report_listing_usable_round_unjudged_agrees(self):
        ev = self.two_rounds(reported("judge-unavailable · unjudged 1 (page 1)", unjudged=[1, 0, 1]))
        a = arms.degraded_check(ev)["agree"]
        self.assertIs(a["judge_unavailable"], True)
        self.assertIs(a["unjudged"], True)

    def test_report_omitting_usable_round_unjudged_disagrees(self):
        a = arms.degraded_check(self.two_rounds(reported("judge-unavailable")))["agree"]
        self.assertIs(a["judge_unavailable"], True)
        self.assertIs(a["unjudged"], False)

    def test_only_judge_unavailable_and_no_unjudged_item_agrees(self):
        ev = ev_of([sr([(A, "SUPPORT"), (B, "SUPPORT")])], [jv((A, "fetch-error"), (B, "fetch-error"))],
                   reported("judge-unavailable"))
        self.assertEqual(arms.degraded_traced(ev)["unjudged"], [0, 0, 0])
        a = arms.degraded_check(ev)["agree"]
        self.assertIs(a["judge_unavailable"], True)
        self.assertIs(a["unjudged"], True)

    def test_only_judge_unavailable_listing_its_fetch_errors_disagrees(self):
        ev = ev_of([sr([(A, "SUPPORT"), (B, "SUPPORT")])], [jv((A, "fetch-error"), (B, "fetch-error"))],
                   reported("judge-unavailable · unjudged 2 (fetch-error 2)", unjudged=[2, 2, 0]))
        self.assertIs(arms.degraded_check(ev)["agree"]["unjudged"], False)

    def test_skipped_judge_and_no_unjudged_item_agrees(self):
        ev = ev_of([sr([(A, "SUPPORT")])], [], reported("judge-unavailable"))
        a = arms.degraded_check(ev)["agree"]
        self.assertIs(a["judge_unavailable"], True)
        self.assertIs(a["unjudged"], True)


if __name__ == "__main__":
    unittest.main()
