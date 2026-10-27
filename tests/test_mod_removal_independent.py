"""Independent tests for removing the Claude Code mod; the skill alone is the product.

Written from the change specification, not from the implementation:

1. No mod files remain; the unrelated git hook hooks/pre-commit stays and is executable.
2. .claude-plugin/plugin.json is valid, names the plugin, carries no mod config; `claude plugin validate .`
   passes (skipped when the CLI is not installed).
3. Tracked text files (outside eval data and tests/) mention none of the mod's identifiers.
4. SKILL.md names only paths that exist and keeps the Step 2.5 judge rules.
5. policy-eval/arms.py ignores mod traffic in a trace and has no mod-only outputs or attributes;
   ingest still works (arms.RUNS is pointed at a temp dir, nothing is written into the repo).
"""

import contextlib
import io
import json
import os
import re
import shutil
import stat
import subprocess
import sys
import tempfile
import unittest

import _pathsetup  # noqa: F401

ROOT = _pathsetup.ROOT
SKILL_DIR = os.path.join(ROOT, "skills", "scored-web-search")
POLICY_EVAL = os.path.join(ROOT, "policy-eval")
if POLICY_EVAL not in sys.path:
    sys.path.insert(0, POLICY_EVAL)

import arms  # noqa: E402


def p(*parts):
    return os.path.join(ROOT, *parts)


# --------------------------------------------------------------------------- 1 files

class TestNoModFiles(unittest.TestCase):

    def test_mod_paths_absent(self):
        for rel in ("hooks/hooks.json", "hooks/register.js", "hooks/view.js", "hooks/register.test.ts",
                    "tests_mod", "types", "tsconfig.json",
                    "skills/scored-web-search/references/mod.md"):
            with self.subTest(path=rel):
                self.assertFalse(os.path.lexists(p(rel)), rel + " still exists")

    def test_mod_paths_not_tracked(self):
        out = subprocess.run(["git", "ls-files", "--", "hooks/hooks.json", "hooks/register.js", "hooks/view.js",
                              "tests_mod", "types", "tsconfig.json",
                              "skills/scored-web-search/references/mod.md"],
                             cwd=ROOT, capture_output=True, text=True, check=True).stdout
        self.assertEqual(out.strip(), "")

    def test_pre_commit_hook_kept_and_executable(self):
        f = p("hooks", "pre-commit")
        self.assertTrue(os.path.isfile(f))
        self.assertTrue(os.access(f, os.X_OK))
        self.assertTrue(os.stat(f).st_mode & stat.S_IXUSR)


# --------------------------------------------------------------------------- 2 manifest

class TestPluginManifest(unittest.TestCase):

    def test_manifest(self):
        with open(p(".claude-plugin", "plugin.json")) as fh:
            m = json.load(fh)
        self.assertIsInstance(m, dict)
        self.assertEqual(m.get("name"), "scored-web-search")
        self.assertNotIn("userConfig", m)
        self.assertNotIn("types", m)

    def test_claude_plugin_validate(self):
        exe = shutil.which("claude")
        if not exe:
            self.skipTest("claude CLI not installed")
        r = subprocess.run([exe, "plugin", "validate", "."], cwd=ROOT, capture_output=True, text=True,
                           timeout=180)
        sys.stderr.write("\n--- claude plugin validate . (exit %d) ---\n%s%s\n---\n"
                         % (r.returncode, r.stdout, r.stderr))
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)


# --------------------------------------------------------------------------- 3 identifiers

FORBIDDEN = ("score_sources", "judge_support", "scored-web-search:searcher", "register.js",
             "references/mod.md", "/search-view", "judgeModel", "tests_mod")
EXCLUDED_PREFIXES = ("policy-eval/runs/", "policy-eval/results/", "policy-eval/fidelity/", "tests/")


class TestNoModIdentifiers(unittest.TestCase):

    def test_tracked_text_files(self):
        files = subprocess.run(["git", "ls-files", "-z"], cwd=ROOT, capture_output=True, check=True
                               ).stdout.decode().split("\0")
        checked, hits = 0, []
        for rel in files:
            if not rel or rel.startswith(EXCLUDED_PREFIXES):
                continue
            path = p(rel)
            if not os.path.isfile(path):          # staged deletion not yet committed
                continue
            with open(path, "rb") as fh:
                raw = fh.read()
            if b"\0" in raw:                       # binary
                continue
            try:
                text = raw.decode("utf-8")
            except UnicodeDecodeError:
                continue
            checked += 1
            for term in FORBIDDEN:
                for i, line in enumerate(text.splitlines(), 1):
                    if term in line:
                        hits.append("%s:%d: %r in %s" % (rel, i, term, line.strip()[:120]))
        self.assertGreater(checked, 10)
        self.assertEqual(hits, [], "\n".join(hits))


# --------------------------------------------------------------------------- 4 SKILL.md

class TestSkillDoc(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        with open(os.path.join(SKILL_DIR, "SKILL.md")) as fh:
            cls.text = fh.read()
        cls.flat = re.sub(r"\s+", " ", cls.text)

    def test_named_paths_exist(self):
        names = {m.rstrip(".,;:)") for m in re.findall(r"(?<![\w/])(?:references|scripts)/[\w./-]+", self.text)}
        self.assertTrue(names, "SKILL.md names no references/ or scripts/ paths")
        for rel in sorted(names):
            with self.subTest(path=rel):
                self.assertTrue(os.path.exists(os.path.join(SKILL_DIR, rel)), rel)

    def step25(self):
        i = self.text.find("Step 2.5")
        self.assertGreaterEqual(i, 0)
        m = re.search(r"^#+ .*Step 2\.5.*$", self.text, re.M)
        self.assertIsNotNone(m, "no Step 2.5 heading")
        rest = self.text[m.end():]
        nxt = re.search(r"^#{1,3} ", rest, re.M)
        return re.sub(r"\s+", " ", rest[:nxt.start()] if nxt else rest)

    def test_step25_lightweight_subagent_judge(self):
        s = self.step25()
        self.assertIn("lightweight subagent", s.lower())
        for v in ("USE", "SKIP", "UNJUDGED"):
            self.assertRegex(s, r"\b%s\b" % v)
        # overview line too
        self.assertRegex(self.flat, r"2\.5\.?\s*Judge.*lightweight subagent.*USE\s*/\s*SKIP\s*/\s*UNJUDGED")

    def test_all_unjudged_fetch_failure_means_unavailable(self):
        s = self.step25()
        low = s.lower()
        self.assertRegex(low, r"every source is unjudged with `fetch-error`")
        self.assertIn("unavailable", low)
        self.assertRegex(low, r"read support sources as before")

    def test_partial_fetch_failure_still_verdicts(self):
        low = self.step25().lower()
        self.assertRegex(low, r"some sources failing to fetch is still a set of verdicts")
        self.assertRegex(low, r"skip and unjudged sources stay out of step 3")

    def test_no_mod_reference(self):
        self.assertNotIn("mod.md", self.text)
        self.assertNotIn("score_sources", self.text)


# --------------------------------------------------------------------------- 5 arms.py

SESSION = "sess-modrm-0001"
SEARCHER = "scored-web-search:searcher"
SCORE_TOOL = "mcp__scored-web-search__score_sources"
JUDGE_TOOL = "mcp__scored-web-search__judge_support"
URL = {
    "main_search": "https://alpha.org/main-search",
    "searcher_seen": "https://bravo.org/searcher-seen",
    "gp_fetch": "https://charlie.org/gp-fetch",
    "task_seen": "https://echo.org/task-seen",
    "main_fetch": "https://delta.org/main-fetch",
    # appear only in stray mod tool traffic -> must not count anywhere
    "score_row": "https://xray.org/score-table-row",
    "judge_row": "https://yankee.org/judge-line",
    "searcher_stub": "https://zulu.org/in-searcher-stub",
}
STRAY = ("score_row", "judge_row", "searcher_stub")
STUB = ('searcher batch X: 2 records saved (not shown). Pass batches: ["X"] to score_sources. '
        "(see " + URL["searcher_stub"] + ")")


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


def links(*keys, summary=""):
    return ('Web search results for query: "q"\n\nLinks: '
            + json.dumps([{"title": "T " + k, "url": URL[k]} for k in keys])
            + ("\n\n" + summary if summary else ""))


def trace_lines():
    L = [{"type": "system", "subtype": "init", "session_id": SESSION}]
    L.append(a_msg("m0", None, [tool_use("sk", "Skill", {"skill": "scored-web-search"})]))
    L.append(result_msg(None, "sk", "skill loaded"))
    L.append(a_msg("m1", None, [tool_use("ws1", "WebSearch", {"query": "main"})]))
    L.append(result_msg(None, "ws1", links("main_search", summary="summary text")))
    # Agent with the old mod's searcher type: an ordinary subagent now
    L.append(a_msg("m2", None, [tool_use("ag1", "Agent", {"subagent_type": SEARCHER, "prompt": "go"})]))
    L.append({"type": "system", "subtype": "task_started", "tool_use_id": "ag1", "subagent_type": SEARCHER,
              "description": "searcher"})
    L.append(a_msg("s1", "ag1", [tool_use("ws2", "WebSearch", {"query": "sub"})], model="claude-haiku-5-5"))
    L.append(result_msg("ag1", "ws2", links("searcher_seen", summary="sub summary")))
    L.append(result_msg(None, "ag1", [{"type": "text", "text": STUB}],
                        tur={"agentId": "S1", "agentType": SEARCHER, "status": "completed"}))
    # Task-named subagent call
    L.append(a_msg("m3", None, [tool_use("tk1", "Task", {"subagent_type": "Explore", "prompt": "go"})]))
    L.append({"type": "system", "subtype": "task_started", "tool_use_id": "tk1", "subagent_type": "Explore",
              "description": "explore"})
    L.append(a_msg("t1", "tk1", [tool_use("ws3", "WebSearch", {"query": "task"})], model="claude-haiku-5-5"))
    L.append(result_msg("tk1", "ws3", links("task_seen")))
    L.append(result_msg(None, "tk1", "done", tur={"agentId": "T1", "agentType": "Explore"}))
    # general-purpose subagent fetch
    L.append(a_msg("m4", None, [tool_use("ag2", "Agent", {"subagent_type": "general-purpose"})]))
    L.append({"type": "system", "subtype": "task_started", "tool_use_id": "ag2",
              "subagent_type": "general-purpose", "description": "helper"})
    L.append(a_msg("g1", "ag2", [tool_use("wf1", "WebFetch", {"url": URL["gp_fetch"]})],
                   model="claude-sonnet-5-5"))
    L.append(result_msg("ag2", "wf1", "page text", tur={"code": 200, "bytes": 9}))
    L.append(result_msg(None, "ag2", "helper done", tur={"agentId": "G1", "agentType": "general-purpose"}))
    # stray mod tool calls
    L.append(a_msg("m5", None, [tool_use("sc1", SCORE_TOOL, {"batches": ["X"], "mode": "academic"})]))
    L.append(result_msg(None, "sc1", "score tier verdict url\n91 1 SUPPORT " + URL["score_row"]
                        + "\n" + json.dumps([{"url": URL["score_row"], "score": 91}])))
    L.append(a_msg("m6", None, [tool_use("jd1", JUDGE_TOOL, {"urls": [URL["judge_row"]]})]))
    L.append(result_msg(None, "jd1", URL["judge_row"] + " | USE | on topic",
                        tur={"usage": {"input_tokens": 500, "output_tokens": 50}, "model": "claude-haiku-5-5"}))
    # main fetch
    L.append(a_msg("m7", None, [tool_use("wf2", "WebFetch", {"url": URL["main_fetch"]})]))
    L.append(result_msg(None, "wf2", "main page", tur={"code": 200, "bytes": 9}))
    L.append({"type": "result", "subtype": "success", "result": "REPORT", "total_cost_usd": 0.05,
              "modelUsage": {"claude-opus-5-5": {"costUSD": 0.04, "webSearchRequests": 1},
                             "claude-haiku-5-5": {"costUSD": 0.01, "webSearchRequests": 2}},
              "num_turns": 8})
    return L


MAIN_SESSION = [a_msg("m1", None, [], usage={"input_tokens": 1000, "output_tokens": 100})]
SUB_SESSIONS = {
    "S1": [a_msg("s1", None, [], model="claude-haiku-5-5", usage={"input_tokens": 1000, "output_tokens": 100})],
    "T1": [a_msg("t1", None, [], model="claude-haiku-5-5", usage={"input_tokens": 500, "output_tokens": 50})],
    "G1": [a_msg("g1", None, [], model="claude-sonnet-5-5", usage={"input_tokens": 200, "output_tokens": 20})],
}


def write_jsonl(path, lines):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as fh:
        for d in lines:
            fh.write(json.dumps(d) + "\n")


def build_run(root):
    trace = os.path.join(root, "trace", "trace.jsonl")
    write_jsonl(trace, trace_lines())
    proj = os.path.join(root, "config", "projects", "-tmp-sbx")
    write_jsonl(os.path.join(proj, SESSION + ".jsonl"), MAIN_SESSION)
    for aid, lines in SUB_SESSIONS.items():
        write_jsonl(os.path.join(proj, SESSION, "subagents", "agent-%s.jsonl" % aid), lines)
    return trace


MOD_EVENT_KEYS = ("searcher_returns", "score_tables", "judge_lines", "judge_usage")


class ModTraceFixture(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp(prefix="modrm-")
        cls.trace = build_run(os.path.join(cls.tmp, "run"))
        cls.ev = arms.extract(cls.trace)

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)


class TestExtractIgnoresMod(ModTraceFixture):

    def test_no_mod_event_keys(self):
        for k in MOD_EVENT_KEYS:
            with self.subTest(key=k):
                self.assertNotIn(k, self.ev)

    def test_events_still_extracted(self):
        self.assertIs(self.ev["skill_fired"], True)
        self.assertEqual(self.ev["report_chars"], len("REPORT"))
        roles = sorted((s["role"], s["query"]) for s in self.ev["searches"])
        self.assertEqual(roles, sorted([("main", "main"), (SEARCHER, "sub"), ("Explore", "task")]))
        self.assertEqual(sorted(f["url"] for f in self.ev["fetches"]),
                         sorted([URL["gp_fetch"], URL["main_fetch"]]))

    def test_stray_tool_urls_contribute_nothing(self):
        lvl = arms.arm_urls(self.ev)
        for k in STRAY:
            with self.subTest(url=k):
                self.assertNotIn(arms.norm(URL[k]), lvl)

    def test_levels(self):
        lvl = arms.arm_urls(self.ev)
        want = {"main_search": "exposed", "searcher_seen": "sub_seen", "task_seen": "sub_seen",
                "gp_fetch": "sub_read", "main_fetch": "read"}
        self.assertEqual(lvl, {arms.norm(URL[k]): v for k, v in want.items()})

    def test_searcher_type_usage_is_subagent(self):
        rows = self.ev["usage_by_role"]
        self.assertEqual(len(rows), 4)
        self.assertEqual(sum(1 for r in rows if r["role"] == "main"), 1)


class TestCostBreakdownNoJudge(ModTraceFixture):

    def test_roles(self):
        c = arms.cost_breakdown(self.ev)
        self.assertTrue(set(c["by_role_usd"]) <= {"main", "subagent"}, c["by_role_usd"])
        self.assertIn("main", c["by_role_usd"])
        self.assertIn("subagent", c["by_role_usd"])

    def test_no_judge_support_keys(self):
        c = arms.cost_breakdown(self.ev)

        def walk(d, path=""):
            for k, v in d.items():
                self.assertFalse(str(k).startswith("judge_support"), path + str(k))
                if isinstance(v, dict):
                    walk(v, path + str(k) + ".")
        walk(c)


class TestAnalyzeArmNoMod(ModTraceFixture):

    def test_no_mod_outputs(self):
        out = arms.analyze_arm(self.ev, {}, {}, {"id": "zz", "mode": "academic", "field": "cs"})
        # judge_vs_blind is back, measured from the judge subagent's lines instead of the mod's tool.
        for k in ("searcher_contract", "rounds"):
            with self.subTest(key=k):
                self.assertNotIn(k, out)
        for k in ("judge_support_fetched", "main_by_in_run_verdict"):
            with self.subTest(key="sources_read." + k):
                self.assertNotIn(k, out["sources_read"])
        self.assertEqual(out["sources_read"]["main_ok"], 1)
        self.assertEqual(out["read_urls"], [arms.norm(URL["main_fetch"])])
        self.assertTrue(set(out["cost"]["by_role_usd"]) <= {"main", "subagent"})


class TestArmsModuleAttributes(unittest.TestCase):

    def test_no_mod_attributes(self):
        for name in ("JUDGE_USAGE_DIR", "BATCH_DIR", "SEARCHER", "JUDGE_TOOL", "SCORE_TOOL",
                     "judge_usage_for", "searcher_records"):
            with self.subTest(attr=name):
                self.assertFalse(hasattr(arms, name), name)


class TestIngestStillWorks(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="modrm-runs-")
        self._saved = arms.RUNS
        arms.RUNS = type(arms.RUNS)(os.path.join(self.tmp, "runs"))
        os.makedirs(str(arms.RUNS))
        self.trace = build_run(os.path.join(self.tmp, "sbx"))
        result = {"cases": [{"name": "mr1", "arms": {
            "with": [{"tracePath": self.trace, "costUsd": 0.05, "turns": 8, "durationSeconds": 20,
                      "error": None, "startedAt": "2026-10-26T00:00:00Z"}],
            "without": [{"tracePath": self.trace, "costUsd": 0.05, "turns": 8, "durationSeconds": 20,
                         "error": None, "startedAt": "2026-10-26T00:01:00Z"}]}}]}
        rp = os.path.join(self.tmp, "result.json")
        with open(rp, "w") as fh:
            json.dump(result, fh)
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            arms.cmd_ingest(rp)

    def tearDown(self):
        arms.RUNS = self._saved
        shutil.rmtree(self.tmp, ignore_errors=True)

    def r(self, name):
        return os.path.join(str(arms.RUNS), name)

    def test_outputs_written(self):
        for arm in ("with", "without"):
            for suffix in ("events.json", "trace.jsonl"):
                with self.subTest(file="mr1.%s.%s" % (arm, suffix)):
                    self.assertTrue(os.path.isfile(self.r("mr1.%s.%s" % (arm, suffix))))
        self.assertTrue(os.path.isfile(self.r("mr1.union.todo.json")))

    def test_events_have_no_mod_keys(self):
        with open(self.r("mr1.with.events.json")) as fh:
            ev = json.load(fh)
        for k in MOD_EVENT_KEYS:
            self.assertNotIn(k, ev)
        self.assertEqual(ev["id"], "mr1")
        self.assertEqual(ev["arm"], "with")

    def test_todo_excludes_stray_urls(self):
        with open(self.r("mr1.union.todo.json")) as fh:
            todo = json.load(fh)
        got = {arms.norm(i["url"]) for i in todo}
        # Stray (mod) URLs stay out, and so now do subagent-only ones: searcher_seen/task_seen (sub_seen) and
        # gp_fetch (sub_read). Only main-context URLs need a blind label.
        want = {arms.norm(URL[k]) for k in ("main_search", "main_fetch")}
        self.assertEqual(got, want)
        for k in STRAY + ("searcher_seen", "task_seen", "gp_fetch"):
            with self.subTest(url=k):
                self.assertNotIn(arms.norm(URL[k]), got)

    def test_repo_runs_untouched(self):
        self.assertFalse(os.path.exists(os.path.join(POLICY_EVAL, "runs", "mr1.with.events.json")))


if __name__ == "__main__":
    unittest.main()
