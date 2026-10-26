"""Independent tests for what policy-eval/arms.py reads out of a scored-web-search skill run.

Written from the specification (the task brief and skills/scored-web-search/SKILL.md), not from the
implementation. Under test: extract()'s score_runs / url_files / agent_returns / peeks / closing, handback_body,
and arm_urls, in_run_verdicts, degraded_traced, degraded_check and analyze_arm's skill-trace fields.

Every trace is synthetic stream-json written into a temp dir (no session transcripts, so usage is empty).
No test needs the network or writes into the repo.
"""

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

SEARCHER = "scored-web-search:searcher"
JUDGE = "general-purpose"
VERDICTS = ("PRIMARY", "SUPPORT", "SKIM", "WEAK", "DROP", "BLOCKED")
SCORER_CMD = "python3 /x/skills/scored-web-search/scripts/srcscore.py --in %s --mode academic --format compact"
LAUNCH = ("Async agent launched successfully. (This tool result is internal metadata.)\n"
          "agentId: %s (use SendMessage to continue)\nThe agent is working in the background.")
FRAME_HEAD = ("[Subagent hand-back] The text below is the final report of a subagent this session delegated to. "
              "The harness indents every line of the report. The report follows:\n")


def handback(reply, aid="agent0"):
    """A foreground Agent tool result as the harness frames it: header, the report with every line indented by
    two spaces (blank lines too), then the harness's own unindented trailer."""
    body = "\n".join("  " + l for l in reply.split("\n"))
    return FRAME_HEAD + body + "\nagentId: %s\n<usage>subagent_tokens: 100\ntool_uses: 2\nduration_ms: 900</usage>" % aid


# --------------------------------------------------------------------------- trace helpers

def a_msg(mid, parent, content, model="claude-opus-5-5"):
    return {"type": "assistant", "parent_tool_use_id": parent,
            "message": {"id": mid, "model": model, "content": content,
                        "usage": {"input_tokens": 1, "output_tokens": 1}}}


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


def row(score, verdict, tier, signals, url):
    """One line of `srcscore.py --format compact` output, in the scorer's own format."""
    return "%5.1f %-8s %-2s %-27s %s" % (score, verdict, tier, signals, url)


def compact(rows, counts, stderr=""):
    """A compact table: header, PRIMARY/SUPPORT rows, counts line; optional stderr line after it."""
    n = sum(counts.get(v, 0) for v in VERDICTS)
    lines = ["SCORE VERDICT T  SIGNALS                     URL"] + list(rows)
    lines.append("-- %d sources | %s" % (n, " | ".join("%s %d" % (v, counts.get(v, 0)) for v in VERDICTS)))
    if stderr:
        lines.append(stderr)
    return "\n".join(lines)


def lookups(f, t):
    return "srcscore: %d of %d external lookups could not be reached" % (f, t)


class Trace:
    """Builds stream-json lines. Main-agent calls have parent None; subagent calls carry the Agent id."""

    def __init__(self):
        self.L = [{"type": "system", "subtype": "init", "session_id": "sess-skill-0001"}]
        self.n = 0

    def _id(self, p):
        self.n += 1
        return "%s%d" % (p, self.n)

    def call(self, name, inp, result, parent=None, is_error=None, tur=None):
        tid = self._id("tu")
        self.L.append(a_msg(self._id("m"), parent, [tool_use(tid, name, inp)]))
        self.L.append(result_msg(parent, tid, result, tur=tur, is_error=is_error))
        return tid

    def bash(self, cmd, result, parent=None, is_error=None):
        return self.call("Bash", {"command": cmd}, result, parent, is_error)

    def score(self, result, parent=None, is_error=None, path="/tmp/ssw/urls-1.json"):
        return self.bash(SCORER_CMD % path, result, parent, is_error)

    def write(self, path, content, parent=None, is_error=None):
        return self.call("Write", {"file_path": path, "content": content},
                         "Error: permission denied" if is_error else "File created successfully at: " + path,
                         parent, is_error)

    def fetch(self, url, parent=None, code=200, text="page text"):
        return self.call("WebFetch", {"url": url, "prompt": "p"}, text, parent,
                         tur={"code": code, "bytes": len(text)})

    def read(self, path, content, parent=None):
        return self.call("Read", {"file_path": path}, content, parent)

    def text(self, parent, text):
        self.L.append(a_msg(self._id("m"), parent, [{"type": "text", "text": text}]))

    def agent(self, subtype, replies=(), body=None, background=False, tool="Agent", parent=None,
              is_error=False, result=None, summary=None):
        """An Agent call: task_started, the subagent's own calls (body(tid)), its text messages, then
        either the foreground tool_result (hand-back frame around the last reply, or `result` verbatim) or,
        in the background, the launch notice right away and a task_notification at the end (its summary the
        last reply's start, or `summary`)."""
        tid = self._id("ag")
        self.L.append(a_msg(self._id("m"), parent, [tool_use(tid, tool, {
            "subagent_type": subtype, "prompt": "go", "run_in_background": background})]))
        self.L.append({"type": "system", "subtype": "task_started", "tool_use_id": tid,
                       "subagent_type": subtype, "is_backgrounded": background, "description": "d"})
        aid = "agent" + tid
        if background:
            self.L.append(result_msg(parent, tid, [{"type": "text", "text": LAUNCH % aid}],
                                     tur={"agentId": aid, "agentType": subtype, "status": "async_launched"}))
        if body:
            body(tid)
        for r in replies:
            self.text(tid, r)
        last = replies[-1] if replies else ""
        if background:
            self.L.append({"type": "system", "subtype": "task_notification", "tool_use_id": tid,
                           "status": "completed", "summary": last[:150] if summary is None else summary})
        elif is_error:
            self.L.append(result_msg(parent, tid, "Agent failed: API error", is_error=True))
        else:
            self.L.append(result_msg(parent, tid, [{"type": "text", "text": handback(last, aid)
                                                    if result is None else result}],
                                     tur={"agentId": aid, "agentType": subtype, "status": "completed"}))
        return tid

    def report(self, text, model_usage=None):
        self.L.append({"type": "result", "subtype": "success", "result": text, "num_turns": 9,
                       "total_cost_usd": 0.01,
                       "modelUsage": model_usage or {"claude-opus-5-5": {"costUSD": 0.01, "webSearchRequests": 0}}})


class TraceCase(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="arms-skill-")
        self.k = 0

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def extract(self, t):
        self.k += 1
        path = os.path.join(self.tmp, "run%d" % self.k, "trace", "trace.jsonl")
        os.makedirs(os.path.dirname(path))
        with open(path, "w") as fh:
            for d in t.L:
                fh.write(json.dumps(d) + "\n")
        return arms.extract(path)

    def closing(self, report):
        t = Trace()
        t.report(report)
        return self.extract(t)["closing"]


def records(*urls):
    return json.dumps([{"url": u, "title": "T"} for u in urls])


# --------------------------------------------------------------------------- 1. extract keys

class TestExtractKeys(TraceCase):

    def test_new_keys_present_on_an_empty_run(self):
        t = Trace()
        t.report("plain report")
        ev = self.extract(t)
        for k in ("score_runs", "url_files", "agent_returns", "peeks", "closing",
                  "searches", "fetches", "skill_fired", "report_chars"):
            self.assertIn(k, ev)
        self.assertEqual(ev["score_runs"], [])
        self.assertEqual(ev["url_files"], [])
        self.assertEqual(ev["agent_returns"], [])
        self.assertEqual(ev["peeks"], [])


# --------------------------------------------------------------------------- 2. score_runs

class TestScoreRuns(TraceCase):

    def test_rows_with_and_without_signals(self):
        sig27 = "a" * 27
        out = compact([row(91.0, "PRIMARY", "1", "cites:120,recent", "https://p.org/one"),
                       row(100.0, "PRIMARY", "1", sig27, "https://p.org/full"),
                       row(64.5, "SUPPORT", "3", "", "https://s.org/two"),
                       row(5.0, "SUPPORT", "0", "x", "https://s.org/low?id=3")],
                      {"PRIMARY": 2, "SUPPORT": 2, "SKIM": 3, "DROP": 1})
        t = Trace()
        t.score(out)
        runs = self.extract(t)["score_runs"]
        self.assertEqual(len(runs), 1)
        self.assertEqual(runs[0]["rows"], [
            {"url": "https://p.org/one", "score": 91.0, "verdict": "PRIMARY", "tier": "1",
             "signals": "cites:120,recent"},
            {"url": "https://p.org/full", "score": 100.0, "verdict": "PRIMARY", "tier": "1", "signals": sig27},
            {"url": "https://s.org/two", "score": 64.5, "verdict": "SUPPORT", "tier": "3", "signals": ""},
            {"url": "https://s.org/low?id=3", "score": 5.0, "verdict": "SUPPORT", "tier": "0", "signals": "x"},
        ])
        self.assertIsInstance(runs[0]["rows"][0]["score"], float)

    def test_counts_line(self):
        out = compact([row(70.0, "SUPPORT", "2", "", "https://s.org/a")],
                      {"PRIMARY": 0, "SUPPORT": 1, "SKIM": 4, "WEAK": 2, "DROP": 3, "BLOCKED": 1})
        t = Trace()
        t.score(out)
        c = self.extract(t)["score_runs"][0]["counts"]
        self.assertEqual(c, {"sources": 11, "PRIMARY": 0, "SUPPORT": 1, "SKIM": 4, "WEAK": 2,
                             "DROP": 3, "BLOCKED": 1})

    def test_exit_zero_when_no_error_and_no_code(self):
        t = Trace()
        t.score(compact([], {"SKIM": 1}))
        self.assertEqual(self.extract(t)["score_runs"][0]["exit"], 0)

    def test_exit_from_leading_exit_code(self):
        t = Trace()
        t.score("Exit code 4\n" + lookups(30, 40) + "\nallow: api.semanticscholar.org", is_error=True)
        t.score("Exit code 2\nusage: srcscore.py [-h] ...", is_error=True)
        runs = self.extract(t)["score_runs"]
        self.assertEqual([r["exit"] for r in runs], [4, 2])
        self.assertEqual(runs[0]["rows"], [])

    def test_lookups_failed_from_stderr_line(self):
        t = Trace()
        t.score(compact([row(80.0, "PRIMARY", "1", "", "https://p.org/x")], {"PRIMARY": 1}, lookups(4, 40)))
        t.score(compact([row(80.0, "PRIMARY", "1", "", "https://p.org/y")], {"PRIMARY": 1}))
        runs = self.extract(t)["score_runs"]
        self.assertEqual(runs[0]["lookups_failed"], [4, 40])
        self.assertIsNone(runs[1]["lookups_failed"])
        self.assertEqual(runs[0]["exit"], 0)

    def test_chars_is_result_length(self):
        out = compact([row(80.0, "PRIMARY", "1", "", "https://p.org/x")], {"PRIMARY": 1, "DROP": 2})
        t = Trace()
        t.score(out)
        self.assertEqual(self.extract(t)["score_runs"][0]["chars"], len(out))

    def test_result_as_text_blocks(self):
        out = compact([row(55.0, "SUPPORT", "2", "hn:40", "https://s.org/b")], {"SUPPORT": 1})
        t = Trace()
        t.score([{"type": "text", "text": out}])
        r = self.extract(t)["score_runs"][0]
        self.assertEqual([x["url"] for x in r["rows"]], ["https://s.org/b"])
        self.assertEqual(r["counts"]["SUPPORT"], 1)

    def test_help_and_other_bash_calls_are_not_score_runs(self):
        t = Trace()
        t.bash("python3 /x/scripts/srcscore.py --help", "usage: srcscore.py ...")
        t.bash("echo $TMPDIR", "/tmp/x")
        t.bash("cat /tmp/ssw/urls-1.json", records("https://a.org"))
        t.score(compact([], {"SKIM": 2}))
        self.assertEqual(len(self.extract(t)["score_runs"]), 1)

    def test_one_entry_per_call_with_role(self):
        t = Trace()
        t.score(compact([row(80.0, "PRIMARY", "1", "", "https://p.org/m")], {"PRIMARY": 1}))
        t.agent(SEARCHER, ["done"], body=lambda tid: t.score(compact([], {"DROP": 1}), parent=tid))
        t.score(compact([], {"SKIM": 1}))
        runs = self.extract(t)["score_runs"]
        self.assertEqual([r["role"] for r in runs], ["main", SEARCHER, "main"])

    def test_input_argument_makes_a_score_run(self):
        out = compact([row(80.0, "PRIMARY", "1", "", "https://p.org/x")], {"PRIMARY": 1})
        for cmd in ("python3 /x/srcscore.py --in /tmp/ssw/urls-1.json --format compact",
                    "python3 /x/srcscore.py --mode news -u https://p.org/x",
                    "python3 /x/srcscore.py --format compact --url https://p.org/x",
                    "python3 /x/srcscore.py --help | head -5; python3 /x/srcscore.py --in /tmp/f.json --mode news"):
            with self.subTest(cmd=cmd):
                t = Trace()
                t.bash(cmd, out)
                runs = self.extract(t)["score_runs"]
                self.assertEqual(len(runs), 1)
                self.assertEqual([x["url"] for x in runs[0]["rows"]], ["https://p.org/x"])

    def test_input_argument_after_a_separator_is_not_srcscores(self):
        for cmd in ("python3 /x/srcscore.py --help",
                    "python3 /x/srcscore.py --help; cat --in",
                    "python3 /x/srcscore.py --help && echo --in /tmp/f.json",
                    "python3 /x/srcscore.py --help & echo -u x",
                    "python3 /x/srcscore.py --help | grep -e --url",
                    "python3 /x/srcscore.py --version\necho --in /tmp/f.json"):
            with self.subTest(cmd=cmd):
                t = Trace()
                t.bash(cmd, "usage: srcscore.py ...")
                self.assertEqual(self.extract(t)["score_runs"], [])

    def test_full_out(self):
        base = "python3 /x/srcscore.py --in /tmp/ssw/urls-1.json --format compact"
        for suffix, want in ((" --full-out /tmp/ssw/full-1.json", "/tmp/ssw/full-1.json"),
                             (" --full-out=/tmp/ssw/full-2.json", "/tmp/ssw/full-2.json"),
                             (" --full-out '/tmp/ssw/full-3.json'", "/tmp/ssw/full-3.json"),
                             (' --full-out="/tmp/ssw/full-4.json"', "/tmp/ssw/full-4.json"),
                             (' --full-out "/tmp/ssw/full-5.json" --mode news', "/tmp/ssw/full-5.json"),
                             ("", None)):
            with self.subTest(suffix=suffix):
                t = Trace()
                t.bash(base + suffix, compact([], {"SKIM": 1}))
                runs = self.extract(t)["score_runs"]
                self.assertEqual(len(runs), 1)
                self.assertIn("full_out", runs[0])
                self.assertEqual(runs[0]["full_out"], want)


# --------------------------------------------------------------------------- 3. url_files

class TestUrlFiles(TraceCase):

    def test_successful_writes_of_url_records(self):
        t = Trace()
        t.agent(SEARCHER, ["/tmp/ssw/urls-1.json 2"],
                body=lambda tid: t.write("/tmp/ssw/urls-1.json", records("https://a.org/1", "https://b.org/2"),
                                         parent=tid))
        t.write("/tmp/ssw/urls-2.json", json.dumps([{"url": "https://c.org/3", "title": "C",
                                                      "date": "2025-01-02"}]))
        files = self.extract(t)["url_files"]
        self.assertEqual(files, [
            {"role": SEARCHER, "path": "/tmp/ssw/urls-1.json", "urls": ["https://a.org/1", "https://b.org/2"]},
            {"role": "main", "path": "/tmp/ssw/urls-2.json", "urls": ["https://c.org/3"]},
        ])

    def test_failed_writes_and_non_url_content_ignored(self):
        t = Trace()
        t.write("/tmp/ssw/urls-1.json", records("https://a.org/1"), is_error=True)
        t.write("/tmp/notes.md", "# Notes\n\nsee https://a.org/1")
        t.write("/tmp/x.json", json.dumps({"url": "https://a.org/1"}))
        t.write("/tmp/y.json", json.dumps(["https://a.org/1", "https://b.org"]))
        t.write("/tmp/z.json", "[]")
        self.assertEqual(self.extract(t)["url_files"], [])


# --------------------------------------------------------------------------- 4. agent_returns

JUDGE_REPLY = "\n".join([
    "https://s.org/use | USE | states the 2024 figure directly",
    "https://s.org/skip | SKIP | off-topic vendor page",
    "https://s.org/page | UNJUDGED | page: empty PDF shell",
    "https://s.org/fe | UNJUDGED | fetch-error: WebFetch failed twice",
    "https://s.org/other | UNJUDGED | paywalled abstract only",
])


class TestAgentReturns(TraceCase):

    def test_judge_reply(self):
        t = Trace()
        t.agent(JUDGE, [JUDGE_REPLY])
        r = self.extract(t)["agent_returns"][0]
        self.assertEqual(r["kind"], "judge")
        self.assertEqual(r["chars"], len(JUDGE_REPLY))
        got = [(arms.norm(v["url"]), v["verdict"], v["reason"], v["kind"]) for v in r["verdicts"]]
        self.assertEqual(got, [
            (arms.norm("https://s.org/use"), "USE", "states the 2024 figure directly", None),
            (arms.norm("https://s.org/skip"), "SKIP", "off-topic vendor page", None),
            (arms.norm("https://s.org/page"), "UNJUDGED", "page: empty PDF shell", "page"),
            (arms.norm("https://s.org/fe"), "UNJUDGED", "fetch-error: WebFetch failed twice", "fetch-error"),
            (arms.norm("https://s.org/other"), "UNJUDGED", "paywalled abstract only", None),
        ])

    def test_inline_records_reply(self):
        reply = "Could not write the file.\n```json\n" + records("https://i.org/1", "https://i.org/2") + "\n```"
        t = Trace()
        t.agent(SEARCHER, [reply])
        r = self.extract(t)["agent_returns"][0]
        self.assertEqual(r["kind"], "inline")
        self.assertEqual(r["urls"], ["https://i.org/1", "https://i.org/2"])
        self.assertEqual(r["chars"], len(reply))

    def test_file_reply(self):
        t = Trace()
        t.agent(SEARCHER, ["/tmp/ssw/urls-1.json 23"])
        r = self.extract(t)["agent_returns"][0]
        self.assertEqual((r["kind"], r["path"], r["n"]), ("file", "/tmp/ssw/urls-1.json", 23))

    def test_other_reply(self):
        t = Trace()
        t.agent("general-purpose", ["All done, nothing to report."])
        r = self.extract(t)["agent_returns"][0]
        self.assertEqual(r["kind"], "other")

    def test_last_text_message_is_the_reply(self):
        t = Trace()
        t.agent(SEARCHER, ["Searching now...", "/tmp/ssw/urls-1.json 7"])
        r = self.extract(t)["agent_returns"][0]
        self.assertEqual((r["kind"], r["n"], r["chars"]), ("file", 7, len("/tmp/ssw/urls-1.json 7")))

    def test_chars_exclude_foreground_handback_wrapper(self):
        reply = "/tmp/ssw/urls-1.json 3"
        t = Trace()
        t.agent(SEARCHER, [reply])
        self.assertEqual(self.extract(t)["agent_returns"][0]["chars"], len(reply))

    def test_background_counts_like_foreground(self):
        reply = "/tmp/ssw/urls-2.json 9"
        t = Trace()
        t.agent(SEARCHER, ["working", reply], background=True)
        t.agent(SEARCHER, [reply])
        bg, fg = self.extract(t)["agent_returns"]
        self.assertEqual(bg["kind"], "file")
        for k in ("kind", "chars", "path", "n"):
            self.assertEqual(bg[k], fg[k], k)

    def test_background_launch_notice_is_not_the_reply(self):
        t = Trace()
        t.agent(JUDGE, [JUDGE_REPLY], background=True)
        r = self.extract(t)["agent_returns"][0]
        self.assertEqual(r["kind"], "judge")
        self.assertEqual(r["chars"], len(JUDGE_REPLY))

    def test_call_order_ids_and_task_tool(self):
        t = Trace()
        a = t.agent(SEARCHER, ["/tmp/ssw/urls-1.json 1"], background=True)
        b = t.agent(SEARCHER, ["/tmp/ssw/urls-2.json 2"], tool="Task")
        c = t.agent(JUDGE, [JUDGE_REPLY])
        rets = self.extract(t)["agent_returns"]
        self.assertEqual([r["tool_use_id"] for r in rets], [a, b, c])
        self.assertEqual([r["kind"] for r in rets], ["file", "file", "judge"])

    def test_agent_calls_by_subagents_are_not_main_returns(self):
        t = Trace()
        outer = t.agent("general-purpose", ["outer done"],
                        body=lambda tid: t.agent(SEARCHER, ["/tmp/ssw/urls-9.json 4"], parent=tid))
        rets = self.extract(t)["agent_returns"]
        self.assertEqual([r["tool_use_id"] for r in rets], [outer])

    def test_agent_with_no_reply(self):
        t = Trace()
        t.agent(JUDGE, [], is_error=True)
        r = self.extract(t)["agent_returns"][0]
        self.assertEqual((r["kind"], r["chars"]), ("other", 0))

    def test_framed_reply_wins_over_last_text(self):
        t = Trace()
        t.agent(SEARCHER, ["Let me write the file now."], result=handback("/tmp/ssw/urls-4.json 12"))
        r = self.extract(t)["agent_returns"][0]
        self.assertEqual((r["kind"], r["path"], r["n"], r["chars"]),
                         ("file", "/tmp/ssw/urls-4.json", 12, len("/tmp/ssw/urls-4.json 12")))

    def test_framed_reply_through_task_tool(self):
        t = Trace()
        t.agent(JUDGE, ["thinking"], tool="Task", result=handback(JUDGE_REPLY))
        r = self.extract(t)["agent_returns"][0]
        self.assertEqual((r["kind"], r["chars"]), ("judge", len(JUDGE_REPLY)))
        self.assertEqual(len(r["verdicts"]), 5)

    def test_framed_multiline_reply_keeps_inner_blank_and_deeper_indent(self):
        reply = "/tmp/ssw/urls-1.json 35\n\nSub-topic 3 has 18 URLs:\n  - below target\nNo dates given."
        t = Trace()
        t.agent(SEARCHER, ["something else"], result=handback(reply))
        r = self.extract(t)["agent_returns"][0]
        self.assertEqual((r["kind"], r["n"], r["chars"]), ("file", 35, len(reply)))

    def test_unframed_tool_result_is_not_the_reply(self):
        reply = "/tmp/ssw/urls-1.json 3"
        t = Trace()
        t.agent(SEARCHER, [reply], result="Some unframed tool output /tmp/other/urls-9.json 99")
        r = self.extract(t)["agent_returns"][0]
        self.assertEqual((r["kind"], r["path"], r["n"], r["chars"]), ("file", "/tmp/ssw/urls-1.json", 3, len(reply)))

    def test_unframed_result_without_text_gives_no_reply(self):
        t = Trace()
        t.agent(JUDGE, [], result=JUDGE_REPLY)
        r = self.extract(t)["agent_returns"][0]
        self.assertEqual((r["kind"], r["chars"]), ("other", 0))

    def test_task_notification_summary_when_no_text(self):
        summary = "/tmp/ssw/urls-3.json 4"
        t = Trace()
        t.agent(SEARCHER, [], background=True, summary=summary)
        r = self.extract(t)["agent_returns"][0]
        self.assertEqual((r["kind"], r["path"], r["n"], r["chars"]), ("file", "/tmp/ssw/urls-3.json", 4, len(summary)))

    def test_last_text_wins_over_task_notification(self):
        t = Trace()
        t.agent(SEARCHER, ["/tmp/ssw/urls-1.json 8"], background=True, summary="/tmp/ssw/urls-7.json 1")
        r = self.extract(t)["agent_returns"][0]
        self.assertEqual((r["path"], r["n"]), ("/tmp/ssw/urls-1.json", 8))


class TestHandbackBody(unittest.TestCase):

    def test_body_without_indent_and_trailer(self):
        reply = "https://s.org/a | USE | ok\n\nhttps://s.org/b | SKIP | off\n  indented in the report"
        self.assertEqual(arms.handback_body(handback(reply, "a1b2")), reply)

    def test_stops_at_first_unindented_nonempty_line(self):
        text = FRAME_HEAD + "  line one\n  line two\nagentId: x\n  not the report\n<usage>u</usage>"
        self.assertEqual(arms.handback_body(text), "line one\nline two")

    def test_launch_notice_is_no_reply(self):
        self.assertFalse(arms.handback_body(LAUNCH % "agentX"))

    def test_unframed_text_is_no_reply(self):
        self.assertFalse(arms.handback_body("/tmp/ssw/urls-1.json 3"))
        # The header without the marker, and the marker without the header.
        self.assertFalse(arms.handback_body("[Subagent hand-back] The text below is a report.\n\n  /tmp/u.json 3"))
        self.assertFalse(arms.handback_body("Note. The report follows:\n  /tmp/u.json 3"))


# --------------------------------------------------------------------------- 4b. peeks

class TestPeeks(TraceCase):

    def build(self):
        """Kept-out files: a written URL file, a URL-file path only a reply names, and a score run's --full-out."""
        t = Trace()
        t.agent(SEARCHER, ["/tmp/ssw/urls-1.json 2"],
                body=lambda tid: (t.write("/tmp/ssw/urls-1.json", records("https://a.org/1", "https://b.org/2"),
                                          parent=tid),
                                  t.bash("cat /tmp/ssw/urls-1.json", records("https://a.org/1"), parent=tid)))
        t.agent(SEARCHER, ["/tmp/ssw/urls-2.json 3"])
        t.bash("python3 /x/srcscore.py --in /tmp/ssw/urls-1.json --in /tmp/ssw/urls-2.json "
               "--full-out /tmp/ssw/full-1.json --format compact", compact([], {"SKIM": 5}))
        return t

    def test_kept_out_files_named_by_path_or_name(self):
        t = self.build()
        cat = records("https://a.org/1", "https://b.org/2")
        t.bash("cat /tmp/ssw/urls-1.json", cat)
        t.bash("cd /tmp/ssw && head -c 100 urls-2.json", "[{\"url\"")
        t.read("/tmp/ssw/full-1.json", "{\"rows\": []}")
        t.bash("jq '.rows[0]' /elsewhere/full-1.json", "null")
        t.bash("python3 /x/srcscore.py --help; wc -c /tmp/ssw/urls-2.json", "usage ...\n412 /tmp/ssw/urls-2.json")
        self.assertCountEqual(self.extract(t)["peeks"], [
            {"tool": "Bash", "what": "cat /tmp/ssw/urls-1.json", "chars": len(cat)},
            {"tool": "Bash", "what": "cd /tmp/ssw && head -c 100 urls-2.json", "chars": len("[{\"url\"")},
            {"tool": "Read", "what": "/tmp/ssw/full-1.json", "chars": len("{\"rows\": []}")},
            {"tool": "Bash", "what": "jq '.rows[0]' /elsewhere/full-1.json", "chars": len("null")},
            {"tool": "Bash", "what": "python3 /x/srcscore.py --help; wc -c /tmp/ssw/urls-2.json",
             "chars": len("usage ...\n412 /tmp/ssw/urls-2.json")},
        ])

    def test_not_peeks(self):
        t = self.build()                                   # the score run names all three files; a subagent cats one
        t.bash("echo $TMPDIR", "/tmp")
        t.bash("ls /tmp/ssw", "notes.txt")
        t.read("/tmp/ssw/notes.md", "notes")
        t.write("/tmp/ssw/urls-1.json", records("https://c.org/3"))      # a Write is not a peek
        t.fetch("https://a.org/1")
        t.bash("python3 /x/srcscore.py -u https://a.org/1 --full-out /tmp/ssw/full-1.json", compact([], {"SKIM": 1}))
        self.assertEqual(self.extract(t)["peeks"], [])

    def test_what_is_truncated_to_300_chars(self):
        t = self.build()
        cmd = "cat /tmp/ssw/urls-1.json | python3 -c '" + "x" * 400 + "'"
        t.bash(cmd, "out")
        self.assertEqual(self.extract(t)["peeks"], [{"tool": "Bash", "what": cmd[:300], "chars": 3}])


# --------------------------------------------------------------------------- 5. closing

FULL_DEGRADED = ("step1-inline 1/3 · lookups-failed 4/40 · judge-unavailable · "
                 "unjudged 3 (fetch-error 2, page 1)")


class TestClosing(TraceCase):

    def test_summary(self):
        c = self.closing("Body.\n\n12 sources collected → 5 passed → 2 judged-out → 3 read\n"
                         "Degraded: none\nKeywords: r1: a, b")
        self.assertEqual(c["summary"], {"collected": 12, "passed": 5, "judged_out": 2, "read": 3})

    def test_summary_absent(self):
        self.assertIsNone(self.closing("Body only.\nDegraded: none")["summary"])

    def test_degraded_absent(self):
        self.assertIsNone(self.closing("Body.\n3 sources collected → 1 passed → 0 judged-out → 1 read")["degraded"])

    def test_degraded_none(self):
        d = self.closing("Body.\nDegraded: none\nKeywords: r1: q")["degraded"]
        self.assertEqual(d["raw"], "none")
        self.assertIs(d["none"], True)
        self.assertIs(d["judge_unavailable"], False)
        for k in ("step1_inline", "lookups_failed", "unjudged"):
            self.assertIsNone(d.get(k), k)

    def test_degraded_all_items(self):
        d = self.closing("x\n1 sources collected → 1 passed → 0 judged-out → 1 read\nDegraded: "
                         + FULL_DEGRADED + "\nKeywords: r1: q")["degraded"]
        self.assertEqual(d["raw"], FULL_DEGRADED)
        self.assertIs(d["none"], False)
        self.assertIs(d["judge_unavailable"], True)
        self.assertEqual(d["step1_inline"], [1, 3])
        self.assertEqual(d["lookups_failed"], [4, 40])
        self.assertEqual(d["unjudged"], [3, 2, 1])

    def test_degraded_partial_items(self):
        d = self.closing("Degraded: lookups-failed 2/30")["degraded"]
        self.assertEqual(d["lookups_failed"], [2, 30])
        self.assertIs(d["judge_unavailable"], False)
        self.assertIsNone(d.get("step1_inline"))
        self.assertIsNone(d.get("unjudged"))

    def test_unjudged_split_lenient(self):
        for item, want in (("unjudged 4 (page 4)", [4, 0, 4]),
                           ("unjudged 1 (page 1, fetch-error 0)", [1, 0, 1]),
                           ("unjudged 2 (fetch-error 2)", [2, 2, 0]),
                           ("unjudged 3 (fetch-error 2, page 1)", [3, 2, 1]),
                           ("unjudged 2", [2, 0, 0]),
                           ("lookups-failed 1/9 · unjudged 5", [5, 0, 0]),
                           ("unjudged 1 (page 1) · unjudged 2 (fetch-error 1, page 1)", [3, 1, 2]),
                           ("unjudged 2 · unjudged 1 (fetch-error 1)", [3, 1, 0])):
            with self.subTest(item=item):
                d = self.closing("Body.\nDegraded: " + item + "\nKeywords: r1: q")["degraded"]
                self.assertEqual(d["raw"], item)
                self.assertEqual(d["unjudged"], want)
                self.assertIs(d["none"], False)

    def test_repeated_counts_are_summed(self):
        d = self.closing("Degraded: lookups-failed 2/68 (round 1) · lookups-failed 2/99 (round 2)")["degraded"]
        self.assertEqual(d["lookups_failed"], [4, 167])
        d = self.closing("Degraded: step1-inline 1/2 (round 1) · step1-inline 0/3 (round 2) · "
                         "lookups-failed 1/10 · judge-unavailable")["degraded"]
        self.assertEqual(d["step1_inline"], [1, 5])
        self.assertEqual(d["lookups_failed"], [1, 10])
        self.assertIs(d["judge_unavailable"], True)
        self.assertIsNone(d.get("unjudged"))

    def test_markdown_decoration(self):
        item = "unjudged 3 (fetch-error 2, page 1)"
        for line in ("`Degraded: %s`" % item, "**Degraded:** %s" % item, "**Degraded: %s**" % item,
                     "- Degraded: %s" % item, "- **Degraded: %s**" % item, "  - `Degraded: %s`  " % item,
                     "> Degraded: %s" % item):
            with self.subTest(line=line):
                d = self.closing("Body.\n" + line + "\nKeywords: r1: q")["degraded"]
                self.assertIsNotNone(d)
                self.assertEqual(d["unjudged"], [3, 2, 1])
                self.assertTrue(d["raw"].rstrip().endswith(")"), d["raw"])
                self.assertIs(d["none"], False)

    def test_markdown_decoration_none(self):
        for line in ("`Degraded: none`", "**Degraded:** none", "**Degraded: none**", "- Degraded: none"):
            with self.subTest(line=line):
                d = self.closing("Body.\n" + line)["degraded"]
                self.assertIs(d["none"], True, d["raw"])

    def test_last_result_line_is_the_report(self):
        t = Trace()
        t.report("draft\n9 sources collected → 9 passed → 0 judged-out → 9 read\nDegraded: judge-unavailable")
        t.report("final\n4 sources collected → 2 passed → 1 judged-out → 1 read\nDegraded: none")
        c = self.extract(t)["closing"]
        self.assertEqual(c["summary"], {"collected": 4, "passed": 2, "judged_out": 1, "read": 1})
        self.assertIs(c["degraded"]["none"], True)
        self.assertIs(c["degraded"]["judge_unavailable"], False)


# --------------------------------------------------------------------------- 6. arm_urls

class TestArmUrlsSkill(TraceCase):

    def test_main_table_rows_and_inline_records_listed(self):
        t = Trace()
        t.agent(SEARCHER, [records("https://www.inline.org/a/")])
        t.agent(SEARCHER, ["/tmp/ssw/urls-1.json 1"],
                body=lambda tid: t.write("/tmp/ssw/urls-1.json", records("https://file-only.org/z"), parent=tid))
        t.score(compact([row(90.0, "PRIMARY", "1", "", "https://www.table.org/p/"),
                         row(60.0, "SUPPORT", "2", "", "https://table.org/s")], {"PRIMARY": 1, "SUPPORT": 1}))
        t.agent(SEARCHER, ["x"], body=lambda tid: t.score(
            compact([row(90.0, "PRIMARY", "1", "", "https://sub-table.org/q")], {"PRIMARY": 1}), parent=tid))
        lvl = arms.arm_urls(self.extract(t))
        self.assertEqual(lvl.get(arms.norm("https://inline.org/a")), "listed")
        self.assertEqual(lvl.get(arms.norm("https://table.org/p")), "listed")
        self.assertEqual(lvl.get(arms.norm("https://table.org/s")), "listed")
        # Only the main agent's table counts; records that stayed in a file never reached main.
        self.assertNotIn(arms.norm("https://sub-table.org/q"), lvl)
        self.assertNotIn(arms.norm("https://file-only.org/z"), lvl)

    def test_higher_level_not_lowered(self):
        t = Trace()
        t.fetch("https://read.org/r")
        t.call("WebSearch", {"query": "q"}, 'Web search results for query: "q"\n\nLinks: '
               + json.dumps([{"title": "E", "url": "https://exposed.org/e"}]) + "\n\nA summary.")
        t.score(compact([row(90.0, "PRIMARY", "1", "", "https://read.org/r"),
                         row(60.0, "SUPPORT", "2", "", "https://exposed.org/e")], {"PRIMARY": 1, "SUPPORT": 1}))
        t.agent(SEARCHER, [records("https://read.org/r", "https://exposed.org/e")])
        t.fetch("https://read.org/r")
        lvl = arms.arm_urls(self.extract(t))
        self.assertEqual(lvl[arms.norm("https://read.org/r")], "read")
        self.assertEqual(lvl[arms.norm("https://exposed.org/e")], "exposed")

    def test_table_row_raises_sub_levels(self):
        t = Trace()
        t.agent(JUDGE, ["https://sub.org/x | USE | ok"], body=lambda tid: t.fetch("https://sub.org/x", parent=tid))
        t.score(compact([row(60.0, "SUPPORT", "2", "", "https://sub.org/x")], {"SUPPORT": 1}))
        lvl = arms.arm_urls(self.extract(t))
        self.assertEqual(lvl[arms.norm("https://sub.org/x")], "listed")


# --------------------------------------------------------------------------- 7. in_run_verdicts

class TestInRunVerdicts(TraceCase):

    def test_table_then_judge_refinement(self):
        t = Trace()
        t.score(compact([row(90.0, "PRIMARY", "1", "", "https://p.org/a"),
                         row(60.0, "SUPPORT", "2", "", "https://www.s.org/use/"),
                         row(60.0, "SUPPORT", "2", "", "https://s.org/skip"),
                         row(60.0, "SUPPORT", "2", "", "https://s.org/page"),
                         row(60.0, "SUPPORT", "2", "", "https://s.org/fe"),
                         row(60.0, "SUPPORT", "2", "", "https://s.org/unjudged-by-none")],
                        {"PRIMARY": 1, "SUPPORT": 5}))
        t.agent(JUDGE, ["\n".join([
            "https://s.org/use | USE | direct",
            "https://s.org/skip | SKIP | off-topic",
            "https://s.org/page | UNJUDGED | page: login wall",
            "https://s.org/fe | UNJUDGED | fetch-error: failed twice",
            "https://p.org/a | SKIP | judge overreach",
            "https://not-in-table.org/z | USE | stray",
        ])])
        v = arms.in_run_verdicts(self.extract(t))
        n = arms.norm
        self.assertEqual(v, {
            n("https://p.org/a"): "PRIMARY",
            n("https://s.org/use"): "SUPPORT/USE",
            n("https://s.org/skip"): "SUPPORT/SKIP",
            n("https://s.org/page"): "SUPPORT/UNJUDGED/page",
            n("https://s.org/fe"): "SUPPORT/UNJUDGED/fetch-error",
            n("https://s.org/unjudged-by-none"): "SUPPORT",
        })

    def test_judge_refines_regardless_of_call_order(self):
        """Verdicts join by URL, not by position in the trace."""
        t = Trace()
        t.agent(JUDGE, ["https://s.org/x | USE | ok"])
        t.score(compact([row(60.0, "SUPPORT", "2", "", "https://s.org/x")], {"SUPPORT": 1}))
        self.assertEqual(arms.in_run_verdicts(self.extract(t)), {arms.norm("https://s.org/x"): "SUPPORT/USE"})


# --------------------------------------------------------------------------- 8/9. degraded

def sr(exit=0, verdicts=(), lf=None, role="main", urls=None):
    """A score run; row i's URL is urls[i], by default https://r.org/<i>."""
    urls = urls or ["https://r.org/%d" % i for i in range(len(verdicts))]
    return {"role": role, "exit": exit, "rows": [{"url": urls[i], "score": 50.0, "verdict": v,
                                                  "tier": "2", "signals": ""} for i, v in enumerate(verdicts)],
            "counts": {}, "lookups_failed": lf, "chars": 10, "full_out": None}


def jv(*kinds, urls=None):
    """A judge return: each item is 'USE', 'SKIP', 'fetch-error' or 'page' (the last two UNJUDGED). Item i's URL
    is urls[i], by default https://j.org/<i> (a URL no sr() row has)."""
    urls = urls or ["https://j.org/%d" % i for i in range(len(kinds))]
    vs = []
    for i, k in enumerate(kinds):
        if k in ("USE", "SKIP"):
            vs.append({"url": urls[i], "verdict": k, "reason": "r", "kind": None})
        else:
            vs.append({"url": urls[i], "verdict": "UNJUDGED", "reason": k + ": r", "kind": k})
    return {"tool_use_id": "j", "chars": 5, "kind": "judge", "verdicts": vs}


R = ["https://r.org/%d" % i for i in range(4)]       # sr()'s default row URLs


FILE_RET = {"tool_use_id": "f", "chars": 3, "kind": "file", "path": "/tmp/u.json", "n": 3}
INLINE_RET = {"tool_use_id": "i", "chars": 30, "kind": "inline", "urls": ["https://i.org"]}
OTHER_RET = {"tool_use_id": "o", "chars": 4, "kind": "other"}


def ev_of(score_runs=(), agent_returns=(), degraded=None, summary=None):
    return {"searches": [], "fetches": [], "score_runs": list(score_runs), "agent_returns": list(agent_returns),
            "url_files": [], "closing": {"summary": summary, "degraded": degraded}}


class TestDegradedTraced(unittest.TestCase):

    def test_step1_inline(self):
        ev = ev_of(agent_returns=[FILE_RET, INLINE_RET, FILE_RET, jv("USE"), OTHER_RET])
        self.assertEqual(arms.degraded_traced(ev)["step1_inline"], [1, 3])

    def test_lookups_failed_summed_over_exit_zero_runs(self):
        ev = ev_of(score_runs=[sr(lf=[2, 20]), sr(lf=[1, 10]), sr(exit=4, lf=[30, 40]), sr()])
        self.assertEqual(arms.degraded_traced(ev)["lookups_failed"], [3, 30])

    def test_clean_run(self):
        ev = ev_of(score_runs=[sr(verdicts=("PRIMARY", "SUPPORT"))],
                   agent_returns=[FILE_RET, jv("USE", "SKIP", urls=[R[1], "https://j.org/x"])])
        self.assertEqual(arms.degraded_traced(ev), {"step1_inline": [0, 1], "lookups_failed": [0, 0],
                                                    "judge_unavailable": False, "unjudged": [0, 0, 0]})

    def test_no_support_rows_needs_no_judge(self):
        ev = ev_of(score_runs=[sr(verdicts=("PRIMARY",))])
        self.assertIs(arms.degraded_traced(ev)["judge_unavailable"], False)

    def test_all_fetch_error_judge_is_unavailable_and_not_counted(self):
        ev = ev_of(score_runs=[sr(verdicts=("SUPPORT",)), sr(verdicts=("SUPPORT",))],
                   agent_returns=[jv("fetch-error", "fetch-error", urls=[R[0], R[1]]),
                                  jv("page", "fetch-error", "USE", urls=["https://j.org/0", "https://j.org/1", R[0]])])
        d = arms.degraded_traced(ev)
        self.assertIs(d["judge_unavailable"], True)
        self.assertEqual(d["unjudged"], [2, 1, 1])

    def test_missing_judge_for_a_support_run(self):
        # The first round's SUPPORT row was judged; the second round's (a different URL) got no verdict.
        ev = ev_of(score_runs=[sr(verdicts=("SUPPORT",), urls=["https://r.org/a"]),
                               sr(verdicts=("SUPPORT", "PRIMARY"), urls=["https://r.org/b", "https://r.org/c"])],
                   agent_returns=[jv("USE", urls=["https://r.org/a"])])
        self.assertIs(arms.degraded_traced(ev)["judge_unavailable"], True)

    def test_failed_score_run_does_not_need_a_judge(self):
        ev = ev_of(score_runs=[sr(verdicts=("SUPPORT",)), sr(exit=4, verdicts=("SUPPORT",), urls=["https://r.org/z"])],
                   agent_returns=[jv("USE", urls=[R[0]])])
        self.assertIs(arms.degraded_traced(ev)["judge_unavailable"], False)

    def test_exact_keys(self):
        for ev in (ev_of(), ev_of(score_runs=[sr(verdicts=("SUPPORT",), lf=[1, 2])], agent_returns=[jv("page")])):
            self.assertEqual(set(arms.degraded_traced(ev)),
                             {"step1_inline", "lookups_failed", "judge_unavailable", "unjudged"})

    def test_same_support_urls_scored_again_after_judging(self):
        ev = ev_of(score_runs=[sr(verdicts=("PRIMARY", "SUPPORT", "SUPPORT")),
                               sr(verdicts=("SUPPORT", "SUPPORT"), urls=[R[1], R[2]])],
                   agent_returns=[jv("USE", "SKIP", urls=[R[1], R[2]])])
        self.assertIs(arms.degraded_traced(ev)["judge_unavailable"], False)

    def test_judge_that_skipped_some_support_urls_is_available(self):
        ev = ev_of(score_runs=[sr(verdicts=("SUPPORT", "SUPPORT", "SUPPORT"))],
                   agent_returns=[jv("USE", urls=[R[1]])])
        self.assertIs(arms.degraded_traced(ev)["judge_unavailable"], False)

    def test_verdicts_matched_by_norm(self):
        ev = ev_of(score_runs=[sr(verdicts=("SUPPORT",), urls=["https://example.org/a"])],
                   agent_returns=[jv("USE", urls=["https://www.example.org/a/"])])
        self.assertIs(arms.degraded_traced(ev)["judge_unavailable"], False)

    def test_judge_verdicts_on_other_urls_only(self):
        ev = ev_of(score_runs=[sr(verdicts=("PRIMARY", "SUPPORT"))], agent_returns=[jv("USE", "SKIP", urls=[R[0], R[3]])])
        self.assertIs(arms.degraded_traced(ev)["judge_unavailable"], True)

    def test_verdict_only_in_an_all_fetch_error_reply_does_not_count(self):
        # Round 1 judged by a usable reply; round 2's URL only appears in an all-fetch-error reply.
        ev = ev_of(score_runs=[sr(verdicts=("SUPPORT",)), sr(verdicts=("SUPPORT",), urls=["https://r.org/b"])],
                   agent_returns=[jv("USE", urls=[R[0]]), jv("fetch-error", urls=["https://r.org/b"])])
        d = arms.degraded_traced(ev)
        self.assertIs(d["judge_unavailable"], True)
        self.assertEqual(d["unjudged"], [0, 0, 0])

    def test_judge_unavailable_per_round_via_trace(self):
        """Two rounds in a real trace: round 2 rescored round 1's SUPPORT URL plus a new one, and its judge
        covered only the new one -- every round has a judged SUPPORT row, so the judge was available."""
        t = Trace()
        t.score(compact([row(60.0, "SUPPORT", "2", "", "https://s.org/a")], {"SUPPORT": 1}))
        t.agent(JUDGE, ["https://s.org/a | USE | ok"])
        t.score(compact([row(60.0, "SUPPORT", "2", "", "https://s.org/a"),
                         row(60.0, "SUPPORT", "2", "", "https://s.org/b")], {"SUPPORT": 2}))
        t.agent(JUDGE, ["https://s.org/b | UNJUDGED | page: login wall"])
        t.score(compact([row(60.0, "SUPPORT", "2", "", "https://s.org/c")], {"SUPPORT": 1}))
        tmp = tempfile.mkdtemp(prefix="arms-skill-")
        try:
            path = os.path.join(tmp, "trace", "trace.jsonl")
            os.makedirs(os.path.dirname(path))
            with open(path, "w") as fh:
                fh.write("".join(json.dumps(d) + "\n" for d in t.L[:-2]))
            ev_two = arms.extract(path)
            with open(path, "w") as fh:
                fh.write("".join(json.dumps(d) + "\n" for d in t.L))
            ev_three = arms.extract(path)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)
        self.assertIs(arms.degraded_traced(ev_two)["judge_unavailable"], False)
        self.assertEqual(arms.degraded_traced(ev_two)["unjudged"], [1, 0, 1])
        # A third round with a new SUPPORT URL and no judge.
        self.assertIs(arms.degraded_traced(ev_three)["judge_unavailable"], True)

    def test_errored_judge_via_trace(self):
        t = Trace()
        t.score(compact([row(60.0, "SUPPORT", "2", "", "https://s.org/a")], {"SUPPORT": 1}))
        t.agent(JUDGE, [], is_error=True)
        t.report("Degraded: judge-unavailable")
        tmp = tempfile.mkdtemp(prefix="arms-skill-")
        try:
            path = os.path.join(tmp, "trace", "trace.jsonl")
            os.makedirs(os.path.dirname(path))
            with open(path, "w") as fh:
                fh.write("".join(json.dumps(d) + "\n" for d in t.L))
            ev = arms.extract(path)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)
        self.assertIs(arms.degraded_traced(ev)["judge_unavailable"], True)
        self.assertIs(arms.degraded_check(ev)["agree"]["judge_unavailable"], True)


def reported(raw, **items):
    d = {"raw": raw, "none": raw == "none", "judge_unavailable": False}
    d.update(items)
    return d


class TestDegradedCheck(unittest.TestCase):

    def test_no_line(self):
        ev = ev_of(score_runs=[sr(lf=[1, 5])])
        self.assertEqual(arms.degraded_check(ev), {"line_present": False, "traced": arms.degraded_traced(ev)})

    def test_none_agrees_with_clean_trace(self):
        ev = ev_of(score_runs=[sr(verdicts=("SUPPORT",))], agent_returns=[FILE_RET, jv("USE", urls=[R[0]])],
                   degraded=reported("none"))
        c = arms.degraded_check(ev)
        self.assertIs(c["line_present"], True)
        self.assertEqual(c["reported"], ev["closing"]["degraded"])
        self.assertEqual(c["traced"], arms.degraded_traced(ev))
        self.assertEqual(c["agree"], {"step1_inline": True, "lookups_failed": True,
                                      "judge_unavailable": True, "unjudged": True})

    def test_none_disagrees_with_degraded_trace(self):
        ev = ev_of(score_runs=[sr(verdicts=("SUPPORT",), lf=[3, 30])],
                   agent_returns=[INLINE_RET, jv("USE", "page", urls=[R[0], "https://j.org/p"])],
                   degraded=reported("none"))
        self.assertEqual(arms.degraded_check(ev)["agree"], {"step1_inline": False, "lookups_failed": False,
                                                            "judge_unavailable": True, "unjudged": False})

    def test_counts_compared_by_first_number(self):
        ev = ev_of(score_runs=[sr(verdicts=("SUPPORT",), lf=[3, 30])],
                   agent_returns=[INLINE_RET, FILE_RET, jv("USE", "page", "fetch-error", urls=R[:3])],
                   degraded=reported("x", step1_inline=[1, 5], lookups_failed=[3, 99], unjudged=[2, 0, 2]))
        self.assertEqual(arms.degraded_check(ev)["agree"], {"step1_inline": True, "lookups_failed": True,
                                                            "judge_unavailable": True, "unjudged": True})

    def test_count_mismatch(self):
        ev = ev_of(score_runs=[sr(verdicts=("SUPPORT",), lf=[3, 30])],
                   agent_returns=[INLINE_RET, jv("USE", "page", urls=R[:2])],
                   degraded=reported("x", step1_inline=[2, 2], lookups_failed=[4, 30], unjudged=[3, 0, 3]))
        self.assertEqual(arms.degraded_check(ev)["agree"], {"step1_inline": False, "lookups_failed": False,
                                                            "judge_unavailable": True, "unjudged": False})

    def test_judge_unavailable_bool_and_unjudged_zeroed(self):
        # Judge unreachable (all fetch-error): report says judge-unavailable and lists no unjudged item.
        ev = ev_of(score_runs=[sr(verdicts=("SUPPORT",))], agent_returns=[jv("fetch-error", "fetch-error")],
                   degraded=reported("judge-unavailable", judge_unavailable=True))
        self.assertEqual(arms.degraded_check(ev)["agree"], {"step1_inline": True, "lookups_failed": True,
                                                            "judge_unavailable": True, "unjudged": True})
        # Usable judge left 1 page UNJUDGED and a second SUPPORT run (new URL) had no judge: unjudged item must
        # still be 0.
        ev = ev_of(score_runs=[sr(verdicts=("SUPPORT",)), sr(verdicts=("SUPPORT",), urls=["https://r.org/new"])],
                   agent_returns=[jv("page", "USE", urls=["https://j.org/p", R[0]])],
                   degraded=reported("judge-unavailable", judge_unavailable=True))
        a = arms.degraded_check(ev)["agree"]
        self.assertIs(a["judge_unavailable"], True)
        self.assertIs(a["unjudged"], True)
        # Report misses judge-unavailable.
        ev["closing"]["degraded"] = reported("none")
        self.assertIs(arms.degraded_check(ev)["agree"]["judge_unavailable"], False)


# --------------------------------------------------------------------------- 10. analyze_arm

class TestAnalyzeArmSkill(TraceCase):

    def setUp(self):
        super().setUp()
        n = arms.norm
        self.table = compact([row(90.0, "PRIMARY", "1", "cites:50", "https://p.org/1"),
                              row(60.0, "SUPPORT", "2", "", "https://s.org/1"),
                              row(60.0, "SUPPORT", "2", "", "https://s.org/2"),
                              row(60.0, "SUPPORT", "2", "", "https://s.org/3"),
                              row(60.0, "SUPPORT", "2", "", "https://s.org/4"),
                              row(60.0, "SUPPORT", "2", "", "https://s.org/5")],
                             {"PRIMARY": 1, "SUPPORT": 5, "SKIM": 4}, lookups(2, 30))
        self.sub_fail = "Exit code 4\n" + lookups(20, 30)
        self.r_file = "/tmp/ssw/urls-1.json 6"
        self.r_inline = records("https://inl.org/1")
        self.r_judge = "\n".join(["https://s.org/1 | USE | direct", "https://s.org/2 | SKIP | off",
                                  "https://s.org/3 | UNJUDGED | page: empty",
                                  "https://s.org/4 | UNJUDGED | fetch-error: twice",
                                  "https://p.org/1 | USE | not a SUPPORT row"])
        self.r_other = "nothing"
        t = Trace()
        t.agent(SEARCHER, [self.r_file], background=True)
        t.agent(SEARCHER, [self.r_inline], background=True,
                body=lambda tid: t.score(self.sub_fail, parent=tid, is_error=True))
        t.score(self.table)
        self.peek_cmd = "head -c 200 /tmp/ssw/urls-1.json"
        self.peek_out = records("https://a.org/1")
        t.bash(self.peek_cmd, self.peek_out)
        t.agent(JUDGE, [self.r_judge], body=lambda tid: t.fetch("https://s.org/1", parent=tid))
        t.agent("general-purpose", [self.r_other])
        for u in ("https://p.org/1", "https://s.org/1", "https://s.org/5", "https://x.org/off-table"):
            t.fetch(u)
        t.fetch("https://s.org/2", code=404, text="Not Found")
        t.report("Body\n10 sources collected → 6 passed → 2 judged-out → 3 read\n"
                 "Degraded: step1-inline 1/2 · unjudged 2 (fetch-error 1, page 1)\nKeywords: r1: q")
        self.ev = self.extract(t)
        self.labels = {n("https://s.org/1"): {"judgment": "useful"}, n("https://s.org/2"): {"judgment": "junk"},
                       n("https://s.org/3"): {"judgment": "marginal"}, n("https://s.org/5"): {"judgment": "useful"}}
        self.out = arms.analyze_arm(self.ev, self.labels, {}, {"id": "t1", "mode": "academic", "field": "ai"})

    def test_judge_vs_blind(self):
        got = {k: dict(v) for k, v in self.out["judge_vs_blind"].items()}
        self.assertEqual(got, {"USE": {"useful": 1}, "SKIP": {"junk": 1}, "UNJUDGED/page": {"marginal": 1},
                               "UNJUDGED/fetch-error": {"unlabeled": 1}})

    def test_main_reads_by_in_run_verdict(self):
        self.assertEqual(dict(self.out["main_reads_by_in_run_verdict"]),
                         {"PRIMARY": 1, "SUPPORT/USE": 1, "SUPPORT": 1, "not-in-table": 1})

    def test_degraded(self):
        d = self.out["degraded"]
        self.assertEqual(d, arms.degraded_check(self.ev))
        self.assertEqual(d["traced"], {"step1_inline": [1, 2], "lookups_failed": [2, 30],
                                       "judge_unavailable": False, "unjudged": [2, 1, 1]})
        self.assertEqual(d["agree"], {"step1_inline": True, "lookups_failed": False,
                                      "judge_unavailable": True, "unjudged": True})

    def assertUrls(self, got, want):
        """The spec names these 'SUPPORT URLs ...' without saying count or collection: accept either."""
        if isinstance(got, int) and not isinstance(got, bool):
            self.assertEqual(got, len(want))
        else:
            self.assertEqual({arms.norm(u) for u in got}, {arms.norm(u) for u in want})

    def test_support_judged_and_without_verdict(self):
        self.assertUrls(self.out["support_judged"],
                        ["https://s.org/1", "https://s.org/2", "https://s.org/3", "https://s.org/4"])
        self.assertUrls(self.out["support_without_verdict"], ["https://s.org/5"])

    def test_peeks(self):
        self.assertEqual(self.out["peeks"], [{"tool": "Bash", "what": self.peek_cmd, "chars": len(self.peek_out)}])
        self.assertEqual(self.out["peeks"], self.ev["peeks"])

    def test_closing_summary_and_exits(self):
        self.assertEqual(self.out["closing_summary"], {"collected": 10, "passed": 6, "judged_out": 2, "read": 3})
        self.assertEqual(self.out["score_exits"], [4, 0])

    def test_main_context_chars(self):
        self.assertEqual(self.out["main_context_chars"], {
            "score_tables": len(self.table), "agent_file": len(self.r_file), "agent_inline": len(self.r_inline),
            "agent_judge": len(self.r_judge), "agent_other": len(self.r_other)})


# --------------------------------------------------------------------------- end to end

class TestEndToEndSkillRun(TraceCase):

    URLS1 = ["https://www.nature.com/articles/n1", "https://blog.example.org/post/", "https://s2.org/b"]
    URLS2 = ["https://acm.org/doi/x", "https://s3.org/c"]

    def build(self):
        t = Trace()
        t.call("Skill", {"skill": "scored-web-search"}, "Launching skill: scored-web-search")
        t.bash("echo $TMPDIR", "/tmp/sbx")

        def searcher(path, urls):
            def body(tid):
                t.call("WebSearch", {"query": "q " + path}, 'Web search results for query: "q"\n\nLinks: '
                       + json.dumps([{"title": "T", "url": u} for u in urls]), parent=tid)
                t.write(path, records(*urls), parent=tid)
            return body

        self.s1 = t.agent(SEARCHER, ["Writing.", "/tmp/sbx/ssw/urls-1.json 3"], background=True,
                          body=searcher("/tmp/sbx/ssw/urls-1.json", self.URLS1))
        self.s2 = t.agent(SEARCHER, ["/tmp/sbx/ssw/urls-2.json 2"], background=True,
                          body=searcher("/tmp/sbx/ssw/urls-2.json", self.URLS2))
        self.table = compact([row(92.0, "PRIMARY", "1", "cites:300,recent", "https://nature.com/articles/n1"),
                              row(88.5, "PRIMARY", "1", "cites:40", "https://acm.org/doi/x"),
                              row(61.0, "SUPPORT", "3", "", "https://blog.example.org/post"),
                              row(58.0, "SUPPORT", "2", "hn:120", "https://s2.org/b"),
                              row(57.5, "SUPPORT", "2", "", "https://s3.org/c")],
                             {"PRIMARY": 2, "SUPPORT": 3})
        t.bash("python3 /x/srcscore.py --in /tmp/sbx/ssw/urls-1.json --in /tmp/sbx/ssw/urls-2.json "
               "--mode academic --format compact", self.table)
        self.verdicts = "\n".join(["https://blog.example.org/post | USE | gives the measured figure",
                                   "https://s2.org/b | SKIP | opinion only",
                                   "https://s3.org/c | UNJUDGED | page: login wall"])

        def judge(tid):
            t.fetch("https://blog.example.org/post", parent=tid)
            t.fetch("https://s2.org/b", parent=tid)
            t.fetch("https://s3.org/c", parent=tid, code=200, text="Sign in")
        self.j = t.agent(JUDGE, [self.verdicts], body=judge)
        for u in ("https://www.nature.com/articles/n1", "https://acm.org/doi/x", "https://blog.example.org/post"):
            t.fetch(u)
        t.report("Findings ... (Nature 2025, PRIMARY 92)\n\n"
                 "5 sources collected → 5 passed → 2 judged-out → 3 read\n"
                 "Degraded: unjudged 1 (fetch-error 0, page 1)\n"
                 "Keywords: r1: q1, q2")
        return t

    def test_full_run(self):
        ev = self.extract(self.build())
        n = arms.norm
        self.assertIs(ev["skill_fired"], True)

        self.assertEqual(len(ev["score_runs"]), 1)
        r = ev["score_runs"][0]
        self.assertEqual((r["role"], r["exit"], r["lookups_failed"], r["chars"], r["full_out"]),
                         ("main", 0, None, len(self.table), None))
        self.assertEqual(ev["peeks"], [])
        self.assertEqual([(x["verdict"], x["url"]) for x in r["rows"]], [
            ("PRIMARY", "https://nature.com/articles/n1"), ("PRIMARY", "https://acm.org/doi/x"),
            ("SUPPORT", "https://blog.example.org/post"), ("SUPPORT", "https://s2.org/b"),
            ("SUPPORT", "https://s3.org/c")])
        self.assertEqual(r["counts"]["sources"], 5)
        self.assertEqual((r["counts"]["PRIMARY"], r["counts"]["SUPPORT"], r["counts"]["DROP"]), (2, 3, 0))

        self.assertEqual(ev["url_files"], [
            {"role": SEARCHER, "path": "/tmp/sbx/ssw/urls-1.json", "urls": self.URLS1},
            {"role": SEARCHER, "path": "/tmp/sbx/ssw/urls-2.json", "urls": self.URLS2}])

        rets = ev["agent_returns"]
        self.assertEqual([x["tool_use_id"] for x in rets], [self.s1, self.s2, self.j])
        self.assertEqual([x["kind"] for x in rets], ["file", "file", "judge"])
        self.assertEqual((rets[0]["path"], rets[0]["n"], rets[0]["chars"]),
                         ("/tmp/sbx/ssw/urls-1.json", 3, len("/tmp/sbx/ssw/urls-1.json 3")))
        self.assertEqual((rets[1]["path"], rets[1]["n"]), ("/tmp/sbx/ssw/urls-2.json", 2))
        self.assertEqual(rets[2]["chars"], len(self.verdicts))
        self.assertEqual([(v["verdict"], v["kind"]) for v in rets[2]["verdicts"]],
                         [("USE", None), ("SKIP", None), ("UNJUDGED", "page")])

        self.assertEqual(ev["closing"]["summary"], {"collected": 5, "passed": 5, "judged_out": 2, "read": 3})
        d = ev["closing"]["degraded"]
        self.assertEqual(d["raw"], "unjudged 1 (fetch-error 0, page 1)")
        self.assertEqual((d["none"], d["judge_unavailable"], d["unjudged"]), (False, False, [1, 0, 1]))

        roles = {(f["role"], n(f["url"])) for f in ev["fetches"]}
        self.assertIn((JUDGE, n("https://s2.org/b")), roles)
        self.assertIn(("main", n("https://acm.org/doi/x")), roles)
        self.assertEqual([s["role"] for s in ev["searches"]], [SEARCHER, SEARCHER])

        lvl = arms.arm_urls(ev)
        self.assertEqual(lvl, {n("https://nature.com/articles/n1"): "read", n("https://acm.org/doi/x"): "read",
                               n("https://blog.example.org/post"): "read", n("https://s2.org/b"): "listed",
                               n("https://s3.org/c"): "listed"})

        self.assertEqual(arms.in_run_verdicts(ev), {
            n("https://nature.com/articles/n1"): "PRIMARY", n("https://acm.org/doi/x"): "PRIMARY",
            n("https://blog.example.org/post"): "SUPPORT/USE", n("https://s2.org/b"): "SUPPORT/SKIP",
            n("https://s3.org/c"): "SUPPORT/UNJUDGED/page"})

        c = arms.degraded_check(ev)
        self.assertEqual(c["traced"], {"step1_inline": [0, 2], "lookups_failed": [0, 0],
                                       "judge_unavailable": False, "unjudged": [1, 0, 1]})
        self.assertEqual(c["agree"], {"step1_inline": True, "lookups_failed": True,
                                      "judge_unavailable": True, "unjudged": True})


if __name__ == "__main__":
    unittest.main()
