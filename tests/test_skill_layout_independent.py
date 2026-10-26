"""Independent spec tests for the self-contained skill folder and single-origin prompts."""

import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest

import _pathsetup  # noqa: F401

ROOT = _pathsetup.ROOT
SKILL = os.path.join(ROOT, "skills", "scored-web-search")
REFS = os.path.join(SKILL, "references")


def read(p):
    with open(p, encoding="utf-8") as f:
        return f.read()


class SkillFolderSelfContained(unittest.TestCase):
    def test_scorer_files_moved(self):
        for rel in ("scripts/srcscore.py", "scripts/srcscore_core/__init__.py", "scripts/policy.json", "scripts/modes"):
            self.assertTrue(os.path.exists(os.path.join(SKILL, rel)), rel)
        root_scripts = sorted(os.listdir(os.path.join(ROOT, "scripts")))
        for gone in ("srcscore.py", "srcscore_core", "modes", "policy.json"):
            self.assertNotIn(gone, root_scripts)
        self.assertIn("check_policy.py", root_scripts)

    def test_copied_skill_folder_scores_offline(self):
        with tempfile.TemporaryDirectory() as tmp:
            dst = os.path.join(tmp, "copied-skill")
            shutil.copytree(SKILL, dst, ignore=shutil.ignore_patterns("__pycache__"))
            inp = os.path.join(tmp, "urls.json")
            with open(inp, "w") as f:
                json.dump([
                    {"url": "https://arxiv.org/abs/2401.00001", "title": "A paper", "date": "2024-01-01"},
                    {"url": "https://some-random-blog.example.com/post", "title": "Blog"},
                ], f)
            for mode in ("academic", "news"):
                r = subprocess.run(
                    [sys.executable, "-I", "scripts/srcscore.py", "--in", inp, "--no-net", "--mode", mode],
                    cwd=dst, capture_output=True, text=True, timeout=120,
                    env={**os.environ, "PYTHONPATH": ""},
                )
                self.assertEqual(r.returncode, 0, r.stderr)
                self.assertIn("arxiv.org", r.stdout)
                self.assertIn("SCORE VERDICT", r.stdout)
                self.assertRegex(r.stdout, r"(?m)^ *\d+\.\d +[A-Z]+ ")

    def test_check_policy_passes(self):
        r = subprocess.run([sys.executable, os.path.join(ROOT, "scripts", "check_policy.py")],
                           cwd=ROOT, capture_output=True, text=True, timeout=120)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)


class SingleOriginPrompts(unittest.TestCase):
    def test_reference_files_exist_nonempty(self):
        for n in ("searcher-prompt.md", "judge-rubric.md"):
            self.assertTrue(read(os.path.join(REFS, n)).strip(), n)

    def test_no_other_copies_in_repo_code(self):
        rubric_line = next(l.strip() for l in read(os.path.join(REFS, "judge-rubric.md")).splitlines() if len(l.strip()) > 40)
        hits = []
        for d, dirs, files in os.walk(ROOT):
            dirs[:] = [x for x in dirs if x not in (".git", "node_modules", "__pycache__", "tests")]
            for fn in files:
                p = os.path.join(d, fn)
                if os.path.abspath(p) == os.path.join(REFS, "judge-rubric.md") or not fn.endswith((".js", ".ts", ".py", ".md", ".json")):
                    continue
                try:
                    if rubric_line in read(p):
                        hits.append(os.path.relpath(p, ROOT))
                except UnicodeDecodeError:
                    pass
        self.assertEqual(hits, [])


class SkillDocs(unittest.TestCase):
    def setUp(self):
        self.skill = read(os.path.join(SKILL, "SKILL.md"))

    def test_skill_md_names_no_mod_tools(self):
        for word in ("references/mod.md", "score_sources", "judge_support", "scored-web-search:searcher"):
            self.assertNotIn(word, self.skill)

    def test_skill_md_script_pipeline(self):
        for ref in ("references/searcher-prompt.md", "references/judge-rubric.md", "scripts/srcscore.py"):
            self.assertIn(ref, self.skill)

    def test_referenced_paths_exist(self):
        for rel in set(re.findall(r"(?:references|scripts)/[\w./-]+\.(?:md|py|json)", self.skill)):
            self.assertTrue(os.path.exists(os.path.join(SKILL, rel)), rel)


if __name__ == "__main__":
    unittest.main()
