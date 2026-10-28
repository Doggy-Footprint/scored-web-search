"""Independent tests for the eval harness model-tier change (Opus -> Sonnet 5.5).

Written from the spec, not from file contents. Covered:

1. policy-eval/make_cases.py generates 21 arms cases, each with execution model claude-sonnet-5-5 and
   no mention of claude-opus-5-5; the checked-in policy-eval/arms/*/case.yaml are byte-identical to a
   fresh generation (made in a temp mirror, never in the repo).
2. Every policy-eval/probe/*/case.yaml has execution model claude-sonnet-5-5 (prompts may still name
   a subagent model "haiku").
3. policy-eval/METHODOLOGY.md "Runs" section names Sonnet 5.5 for the blind judge (Run 1) and the arms
   (Run 2), and Run 4 (union labeller) still says it uses the same model as Run 1.

No network; nothing is written into the repo.
"""

import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest

import _pathsetup  # noqa: F401

ROOT = _pathsetup.ROOT
PE = os.path.join(ROOT, "policy-eval")
NEW, OLD = "claude-sonnet-5-5", "claude-opus-5-5"


def read(path):
    with open(path, "rb") as fh:
        return fh.read().decode("utf-8")


def execution_model(text):
    m = re.search(r"^execution:\s*\n((?:[ \t]+.*\n?|\n)*)", text, re.M)
    assert m, "no execution block"
    mm = re.search(r"^[ \t]+model:\s*(\S+)\s*$", m.group(1), re.M)
    return mm.group(1) if mm else None


class TestArmsCases(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp(prefix="model-tiers-")
        pe = os.path.join(cls.tmp, "policy-eval")
        os.makedirs(pe)
        shutil.copyfile(os.path.join(PE, "make_cases.py"), os.path.join(pe, "make_cases.py"))
        shutil.copyfile(os.path.join(PE, "questions.json"), os.path.join(pe, "questions.json"))
        cls.proc = subprocess.run([sys.executable, "make_cases.py"], cwd=pe, capture_output=True, text=True)
        cls.gen = os.path.join(pe, "arms")

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def test_generation_ok_and_21_cases(self):
        self.assertEqual(self.proc.returncode, 0, self.proc.stderr)
        self.assertEqual(len(os.listdir(self.gen)), 21)

    def test_generated_model_is_sonnet_only(self):
        for cid in sorted(os.listdir(self.gen)):
            with self.subTest(case=cid):
                y = read(os.path.join(self.gen, cid, "case.yaml"))
                self.assertEqual(execution_model(y), NEW)
                self.assertNotIn(OLD, y)

    def test_checked_in_arms_match_generation(self):
        repo = os.path.join(PE, "arms")
        self.assertEqual(sorted(os.listdir(self.gen)),
                         sorted(d for d in os.listdir(repo) if os.path.isfile(os.path.join(repo, d, "case.yaml"))))
        for cid in sorted(os.listdir(self.gen)):
            with self.subTest(case=cid):
                self.assertEqual(read(os.path.join(repo, cid, "case.yaml")),
                                 read(os.path.join(self.gen, cid, "case.yaml")))

    def test_checked_in_arms_never_name_opus(self):
        repo = os.path.join(PE, "arms")
        for cid in sorted(os.listdir(repo)):
            p = os.path.join(repo, cid, "case.yaml")
            if os.path.isfile(p):
                with self.subTest(case=cid):
                    self.assertNotIn(OLD, read(p))


class TestProbeCases(unittest.TestCase):

    def test_every_probe_uses_sonnet(self):
        probe = os.path.join(PE, "probe")
        ids = sorted(d for d in os.listdir(probe) if os.path.isdir(os.path.join(probe, d)))
        self.assertTrue(ids)
        for pid in ids:
            with self.subTest(probe=pid):
                y = read(os.path.join(probe, pid, "case.yaml"))
                self.assertEqual(execution_model(y), NEW)
                self.assertNotIn(OLD, y)


class TestMethodologyRuns(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        text = read(os.path.join(PE, "METHODOLOGY.md"))
        m = re.search(r"^## Runs.*?\n(.*?)(?=^## )", text, re.M | re.S)
        assert m, "no Runs section"
        cls.runs = m.group(1)

    def item(self, n):
        m = re.search(r"^%d\. (.*?)(?=^\d+\. |\Z)" % n, self.runs, re.M | re.S)
        self.assertIsNotNone(m, "Run %d missing" % n)
        return m.group(1)

    def test_run1_judge_is_sonnet(self):
        r = self.item(1)
        self.assertIn("Blind judge", r)
        self.assertIn("Sonnet 5.5", r)
        self.assertNotIn("Opus", r)

    def test_run2_arms_is_sonnet(self):
        r = self.item(2)
        self.assertIn("Arms", r)
        self.assertIn("Sonnet 5.5", r)
        self.assertNotIn("Opus", r)

    def test_run4_same_model_as_run1(self):
        r = self.item(4)
        self.assertIn("Union labeller", r)
        self.assertRegex(r, r"same model[^.]*\b1\b")

    def test_runs_section_names_no_opus(self):
        self.assertNotIn("Opus", self.runs)


if __name__ == "__main__":
    unittest.main()
