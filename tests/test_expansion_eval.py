"""Independent tests for the policy-eval question set and contrast.py round breakdown.

Written from the specification, not from the implementation. Two things are under test:

A. policy-eval/questions.json  -- shape, id/field integrity, the expansion marker.
B. policy-eval/contrast.py     -- the per-round ("expansion") block: absent for one-shot
   runs, present and correctly counted for two-round runs, and never disturbing the
   top-level metrics.

contrast.py hardcodes RUNS = <its own dir>/runs and shells out to ../scripts/srcscore.py
*without* --no-net, so B's fixtures run through the real scorer. To avoid leaving residue
in the repo's runs/ directory the whole harness is mirrored into a temp dir:

    <tmp>/scripts   -> symlink to the real scripts/
    <tmp>/policy-eval/contrast.py  -> byte copy of the real contrast.py
    <tmp>/policy-eval/runs/        -> fixtures

contrast.py is copied verbatim, never edited. The fixture URLs are chosen from domains
that the scorer resolves from the tier table alone (no preprint/DOI/citation lookup), so
the scores below are stable with or without a network. See the module docstring note in
test_contrast_round_breakdown if that ever stops being true.
"""

import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

import _pathsetup  # noqa: F401  (sys.path bootstrap for scripts/)

ROOT = _pathsetup.ROOT
SCRIPTS = _pathsetup.SCRIPTS
POLICY_EVAL = os.path.join(ROOT, "policy-eval")
QUESTIONS_PATH = os.path.join(POLICY_EVAL, "questions.json")
CONTRAST_PATH = os.path.join(POLICY_EVAL, "contrast.py")

EXPECTED_MODES = ["academic", "non-academic", "community-opinion", "news", "official-docs"]
MODE_PREFIX = {"academic": "ac", "non-academic": "na", "community-opinion": "co",
               "news": "nw", "official-docs": "od"}
QUESTIONS_PER_MODE = 6


def load_questions():
    with open(QUESTIONS_PATH) as fh:
        return json.load(fh)


def base_policy_fields():
    """The citation half-life fields defined by scripts/policy.json itself."""
    with open(os.path.join(SCRIPTS, "policy.json")) as fh:
        return set(json.load(fh)["field_halflife_years"])


def mode_fields(mode):
    """Fields valid for `srcscore.py --mode <mode>`: base policy plus the mode overlay.

    Mirrors srcscore_core.policy.load_mode_overlay's mode->filename rule ('-' -> '_').
    """
    fields = base_policy_fields()
    overlay_path = os.path.join(SCRIPTS, "modes", mode.replace("-", "_") + ".json")
    if os.path.exists(overlay_path):
        with open(overlay_path) as fh:
            fields |= set(json.load(fh).get("field_halflife_years", {}))
    return fields


def all_questions(doc):
    for mode, block in doc["modes"].items():
        for q in block["questions"]:
            yield mode, q


# --------------------------------------------------------------------------- A


class TestQuestionsJson(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.doc = load_questions()

    def test_is_valid_json_with_readme_and_modes(self):
        self.assertIsInstance(self.doc, dict)
        self.assertIn("_readme", self.doc)
        self.assertTrue(str(self.doc["_readme"]).strip(), "_readme must be non-empty")
        self.assertIn("modes", self.doc)
        self.assertIsInstance(self.doc["modes"], dict)

    def test_exactly_the_five_expected_modes(self):
        self.assertEqual(sorted(self.doc["modes"]), sorted(EXPECTED_MODES))

    def test_each_mode_has_field_default_and_questions(self):
        for mode, block in self.doc["modes"].items():
            with self.subTest(mode=mode):
                self.assertIn("field_default", block)
                self.assertIsInstance(block["field_default"], str)
                self.assertTrue(block["field_default"].strip())
                self.assertIn("questions", block)
                self.assertIsInstance(block["questions"], list)

    def test_six_questions_per_mode(self):
        for mode, block in self.doc["modes"].items():
            with self.subTest(mode=mode):
                self.assertEqual(len(block["questions"]), QUESTIONS_PER_MODE)

    def test_question_ids_globally_unique(self):
        ids = [q["id"] for _, q in all_questions(self.doc)]
        dupes = sorted({i for i in ids if ids.count(i) > 1})
        self.assertEqual(dupes, [], "duplicate question ids: %s" % dupes)
        self.assertEqual(len(ids), len(EXPECTED_MODES) * QUESTIONS_PER_MODE)

    def test_question_ids_match_mode_prefix_and_digit(self):
        for mode, q in all_questions(self.doc):
            with self.subTest(qid=q.get("id")):
                prefix = MODE_PREFIX[mode]
                self.assertRegex(q["id"], r"^%s\d$" % prefix)

    def test_required_string_keys_non_empty(self):
        for mode, q in all_questions(self.doc):
            for key in ("id", "field", "q", "stress"):
                with self.subTest(qid=q.get("id"), key=key):
                    self.assertIn(key, q)
                    self.assertIsInstance(q[key], str)
                    self.assertTrue(q[key].strip(), "%s.%s is empty" % (q.get("id"), key))

    def test_question_fields_known_to_their_mode(self):
        """Correct reading: a question's field must be accepted by srcscore under its mode.

        Field validity is mode-dependent -- `--mode community-opinion --field opinion`
        works, `--mode academic --field opinion` exits 2 -- so the mode overlay is part of
        "what the policy knows".
        """
        for mode, q in all_questions(self.doc):
            with self.subTest(qid=q.get("id"), mode=mode):
                self.assertIn(q["field"], mode_fields(mode))

    def test_field_defaults_known_to_their_mode(self):
        for mode, block in self.doc["modes"].items():
            with self.subTest(mode=mode):
                self.assertIn(block["field_default"], mode_fields(mode))

    def test_exactly_one_expansion_question_per_mode(self):
        for mode, block in self.doc["modes"].items():
            with self.subTest(mode=mode):
                marked = [q["id"] for q in block["questions"] if q.get("expansion")]
                self.assertEqual(len(marked), 1, "expected 1 expansion question, got %s" % marked)

    def test_expansion_ids_are_the_sixth_of_each_mode(self):
        marked = sorted(q["id"] for _, q in all_questions(self.doc) if "expansion" in q)
        self.assertEqual(marked, ["ac6", "co6", "na6", "nw6", "od6"])

    def test_expansion_key_is_true_and_absent_elsewhere(self):
        for _, q in all_questions(self.doc):
            with self.subTest(qid=q.get("id")):
                if q["id"].endswith("6"):
                    self.assertIs(q.get("expansion"), True)
                else:
                    self.assertNotIn("expansion", q,
                                     "%s must not carry the expansion key at all" % q["id"])


# --------------------------------------------------------------------------- B

# Fixture URLs. Each resolves from the domain tier table alone, so the verdict is stable.
#   tier 1  nist.gov          score 88  PRIMARY   -> policy positive
#   tier 2  rand.org          score 74  SUPPORT   -> policy positive
#   tier 4  infoq.com         score 46  SKIM      -> neither
#   tier 5  reddit.com        score 32  WEAK      -> policy negative, registered
#   tier 5  medium.com        score 32  WEAK      -> policy negative, registered
#   tier 5  (unregistered)    score 32  WEAK      -> policy negative, matched is null
#   tier 6  geeksforgeeks.org score 14  DROP      -> policy negative
U_NIST = "https://www.nist.gov/publications/synthetic-fixture-a"
U_RAND = "https://www.rand.org/pubs/research_reports/RRA0001.html"
U_INFOQ = "https://www.infoq.com/articles/synthetic-fixture-b/"
U_REDDIT = "https://www.reddit.com/r/MachineLearning/comments/zzfixture/synthetic/"
U_MEDIUM = "https://medium.com/@fixture/synthetic-fixture-c-000000000000"
U_UNREG = "https://synthetic-fixture-domain-zzq.example/page/one"
U_GFG = "https://www.geeksforgeeks.org/synthetic-fixture-d/"
U_NATURE = "https://www.nature.com/articles/s41586-020-2649-2"


def judged(url, rank, judgment, round_=None, **extra):
    entry = {"url": url, "rank": rank, "title": "fixture", "judgment": judgment,
             "vendor_interest": False, "has_primary_data": False, "basis": "snippet",
             "why": "fixture"}
    if round_ is not None:
        entry["round"] = round_
    entry.update(extra)
    return entry


# Round 1: nist(useful,pos) rand(junk,pos) reddit(useful,neg) infoq(useful,skim)
# Round 2: nature(useful,pos) medium(useful,neg) unreg(useful,neg) gfg(junk,neg)
ROUNDED = [
    judged(U_NIST, 1, "useful", 1),
    judged(U_RAND, 2, "junk", 1),
    judged(U_REDDIT, 3, "useful", 1),
    judged(U_INFOQ, 4, "useful", 1),
    judged(U_NATURE, 1, "useful", 2),
    judged(U_MEDIUM, 2, "useful", 2),
    judged(U_UNREG, 3, "useful", 2),
    judged(U_GFG, 4, "junk", 2),
]
FLAT = [{k: v for k, v in e.items() if k != "round"} for e in ROUNDED]

KEYWORDS = [{"keyword": "catastrophic forgetting",
             "source_url": U_NATURE}]


class ContrastHarness(unittest.TestCase):
    """Runs the real contrast.py from a temp mirror so no fixture lands in the repo."""

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp(prefix="contrast-eval-")
        os.symlink(SCRIPTS, os.path.join(cls.tmp, "scripts"))
        cls.pe = os.path.join(cls.tmp, "policy-eval")
        cls.runs = os.path.join(cls.pe, "runs")
        os.makedirs(cls.runs)
        shutil.copyfile(CONTRAST_PATH, os.path.join(cls.pe, "contrast.py"))

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def run_contrast(self, run_id, entries, mode="academic", field="ai", top_level=None):
        doc = {"id": run_id, "question": "fixture question", "judged": entries}
        if top_level:
            doc.update(top_level)
        with open(os.path.join(self.runs, run_id + ".judge.json"), "w") as fh:
            json.dump(doc, fh)
        with open(os.path.join(self.runs, run_id + ".urls.txt"), "w") as fh:
            fh.write("\n".join(e["url"] for e in entries) + "\n")
        proc = subprocess.run(
            [sys.executable, "contrast.py", run_id, "--mode", mode, "--field", field],
            cwd=self.pe, capture_output=True, text=True)
        self.assertEqual(proc.returncode, 0,
                         "contrast.py failed:\n%s\n%s" % (proc.stdout, proc.stderr))
        with open(os.path.join(self.runs, run_id + ".contrast.json")) as fh:
            return json.load(fh), proc.stdout


class TestContrastNoRounds(ContrastHarness):

    def test_report_has_no_expansion_key(self):
        report, _ = self.run_contrast("zzflat", FLAT)
        self.assertNotIn("expansion", report)

    def test_scoring_fixtures_behave_as_assumed(self):
        """Guards the rest of the suite: if the scorer's verdicts drift, fail here."""
        report, _ = self.run_contrast("zzsanity", FLAT)
        self.assertEqual(report["n_scored"], 8)
        self.assertEqual(report["n_unscored"], 0)
        self.assertEqual(report["judge_counts"], {"useful": 6, "marginal": 0, "junk": 2})
        # 3 useful sources land on the policy's negative side (reddit, medium, unreg);
        # 2 useful land positive (nist, nature) -> 3/5.
        self.assertEqual(report["recall_loss"]["n"], 3)
        self.assertEqual(report["recall_loss"]["rate"], 0.6)
        # 1 junk promoted (rand), 1 junk correctly dropped (gfg) -> 1/2.
        self.assertEqual(report["precision_loss"]["n"], 1)
        self.assertEqual(report["precision_loss"]["rate"], 0.5)
        self.assertEqual(report["skim_band"], {"useful": 1, "junk": 0})


class TestContrastRoundBreakdown(ContrastHarness):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.report, cls.stdout = None, None

    def rounded(self):
        report, stdout = self.run_contrast(
            "zzrounds", ROUNDED, top_level={"expansion_keywords": KEYWORDS})
        return report, stdout

    def test_expansion_block_present(self):
        report, _ = self.rounded()
        self.assertIn("expansion", report)
        self.assertEqual(sorted(report["expansion"]), ["by_round", "keywords"])

    def test_keywords_copied_from_judge_file(self):
        report, _ = self.rounded()
        self.assertEqual(report["expansion"]["keywords"], KEYWORDS)

    def test_keywords_default_to_empty_list(self):
        report, _ = self.run_contrast("zzrounds_nokw", ROUNDED)
        self.assertEqual(report["expansion"]["keywords"], [])

    def test_by_round_keyed_by_string_round_number(self):
        report, _ = self.rounded()
        self.assertEqual(sorted(report["expansion"]["by_round"]), ["1", "2"])
        for key in report["expansion"]["by_round"]:
            self.assertIsInstance(key, str)

    def test_round_one_counts(self):
        report, _ = self.rounded()
        r1 = report["expansion"]["by_round"]["1"]
        self.assertEqual(r1["n"], 4)
        self.assertEqual(r1["useful"], 3)                      # nist, reddit, infoq
        self.assertEqual(r1["recall_loss"]["n"], 1)            # reddit
        self.assertEqual(r1["recall_loss"]["rate"], 0.5)       # 1 / (1 lost + 1 kept:nist)
        self.assertEqual(r1["tier5"], 1)                       # reddit
        self.assertEqual(r1["unregistered"], 0)

    def test_round_two_counts(self):
        report, _ = self.rounded()
        r2 = report["expansion"]["by_round"]["2"]
        self.assertEqual(r2["n"], 4)
        self.assertEqual(r2["useful"], 3)                      # nature, medium, unreg
        self.assertEqual(r2["recall_loss"]["n"], 2)            # medium, unreg
        self.assertEqual(r2["recall_loss"]["rate"], 0.667)     # 2/3, rounded to 3dp
        self.assertEqual(r2["tier5"], 2)                       # medium, unreg
        self.assertEqual(r2["unregistered"], 1)                # unreg only

    def test_per_round_keys_exactly_as_specified(self):
        report, _ = self.rounded()
        for n, block in report["expansion"]["by_round"].items():
            with self.subTest(round=n):
                self.assertEqual(sorted(block),
                                 ["n", "recall_loss", "tier5", "unregistered", "useful"])
                self.assertEqual(sorted(block["recall_loss"]), ["n", "rate"])

    def test_round_ns_sum_to_scored_rows(self):
        report, _ = self.rounded()
        total = sum(b["n"] for b in report["expansion"]["by_round"].values())
        self.assertEqual(total, report["n_scored"])

    def test_top_level_metrics_identical_with_and_without_rounds(self):
        """Rounds must not change any top-level number -- they are still computed over all rows.

        The `cases` arrays are echoes of the judged entries and therefore legitimately
        carry the extra `round` key, so they are compared by URL rather than verbatim.
        """
        rounded, _ = self.rounded()
        flat, _ = self.run_contrast("zzflat_cmp", FLAT)

        def strip(rep):
            out = {k: v for k, v in rep.items() if k not in ("id", "expansion")}
            for key in ("recall_loss", "precision_loss", "vendor"):
                out[key] = {k: ([c["url"] for c in v] if k == "cases" else v)
                            for k, v in out[key].items()}
            return out

        self.assertEqual(strip(rounded), strip(flat))

    def test_stdout_reports_each_round(self):
        _, stdout = self.rounded()
        self.assertIn("round 1", stdout)
        self.assertIn("round 2", stdout)


if __name__ == "__main__":
    unittest.main()
