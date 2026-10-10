import os
import shutil
import subprocess
import tempfile
import unittest
import zipfile

import _pathsetup  # noqa: F401
from _pathsetup import ROOT

SCRIPT = os.path.join(ROOT, "scripts", "build_skill_zip.sh")
WORKFLOW = os.path.join(ROOT, ".github", "workflows", "skill-zip.yml")
SKILL_DIR = os.path.join(ROOT, "skills", "scored-web-search")
DEFAULT_OUT = os.path.join(ROOT, "dist", "scored-web-search.zip")


def run(script, *args, check=True):
    return subprocess.run(["bash", script, *args], capture_output=True,
                          text=True, check=check)


class BuildZipTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp, True)

    def names(self, path):
        with zipfile.ZipFile(path) as z:
            return z.namelist()

    def test_structure(self):
        out = os.path.join(self.tmp, "o.zip")
        run(SCRIPT, out)
        names = self.names(out)
        self.assertIn("scored-web-search/SKILL.md", names)
        tops = {n.split("/")[0] for n in names}
        self.assertEqual(tops, {"scored-web-search"})
        for n in names:
            self.assertTrue(n.startswith("scored-web-search/"), n)

    def test_no_junk_in_real_build(self):
        out = os.path.join(self.tmp, "o.zip")
        run(SCRIPT, out)
        for n in self.names(out):
            parts = n.split("/")
            self.assertNotIn("__pycache__", parts, n)
            self.assertFalse(n.endswith(".pyc"), n)
            self.assertNotEqual(parts[-1], ".DS_Store", n)

    def test_excludes_junk_in_temp_copy(self):
        fake = os.path.join(self.tmp, "repo")
        os.makedirs(os.path.join(fake, "scripts"))
        shutil.copy(SCRIPT, os.path.join(fake, "scripts"))
        skill = os.path.join(fake, "skills", "scored-web-search")
        os.makedirs(os.path.join(skill, "scripts", "__pycache__"))
        os.makedirs(os.path.join(skill, "__pycache__"))
        files = {
            "SKILL.md": "x",
            "scripts/keep.py": "x",
            "scripts/__pycache__/a.cpython-311.pyc": "x",
            "__pycache__/b.pyc": "x",
            "stray.pyc": "x",
            "scripts/stray2.pyc": "x",
            ".DS_Store": "x",
            "scripts/.DS_Store": "x",
        }
        for rel, c in files.items():
            with open(os.path.join(skill, rel), "w") as f:
                f.write(c)
        out = os.path.join(self.tmp, "c.zip")
        run(os.path.join(fake, "scripts", "build_skill_zip.sh"), out)
        names = set(self.names(out))
        self.assertIn("scored-web-search/SKILL.md", names)
        self.assertIn("scored-web-search/scripts/keep.py", names)
        for n in names:
            self.assertNotIn("__pycache__", n)
            self.assertFalse(n.endswith(".pyc"), n)
            self.assertFalse(n.endswith(".DS_Store"), n)

    def test_overwrites_existing(self):
        out = os.path.join(self.tmp, "o.zip")
        with open(out, "w") as f:
            f.write("not a zip")
        run(SCRIPT, out)
        self.assertTrue(zipfile.is_zipfile(out))
        # rebuilding again must not append duplicates
        first = self.names(out)
        run(SCRIPT, out)
        self.assertEqual(first, self.names(out))

    def test_creates_parent_dirs(self):
        out = os.path.join(self.tmp, "a", "b", "c", "o.zip")
        run(SCRIPT, out)
        self.assertTrue(zipfile.is_zipfile(out))

    def test_default_output(self):
        dist = os.path.join(ROOT, "dist")
        existed = os.path.exists(dist)
        had_zip = os.path.exists(DEFAULT_OUT)
        if had_zip:
            self.skipTest("default output already exists; not touching it")
        try:
            run(SCRIPT)
            self.assertTrue(zipfile.is_zipfile(DEFAULT_OUT))
            self.assertIn("scored-web-search/SKILL.md", self.names(DEFAULT_OUT))
        finally:
            if os.path.exists(DEFAULT_OUT):
                os.remove(DEFAULT_OUT)
            if not existed and os.path.isdir(dist) and not os.listdir(dist):
                os.rmdir(dist)


class WorkflowTests(unittest.TestCase):
    def setUp(self):
        try:
            import yaml
        except ImportError:
            self.skipTest("PyYAML not available")
        with open(WORKFLOW) as f:
            self.wf = yaml.safe_load(f)

    def test_trigger(self):
        on = self.wf.get("on", self.wf.get(True))  # YAML 1.1 parses `on` as True
        push = on["push"]
        self.assertIn("master", push["branches"])
        self.assertIn("skills/scored-web-search/**", push["paths"])

    def test_step_order(self):
        steps = []
        for job in self.wf["jobs"].values():
            steps += [s.get("run", "") for s in job["steps"]]
        def idx(needle):
            for i, s in enumerate(steps):
                if needle in s:
                    return i
            self.fail("missing step: " + needle)
        ut, cp, bd = idx("unittest"), idx("scripts/check_policy.py"), idx("build_skill_zip.sh")
        self.assertLess(ut, bd)
        self.assertLess(cp, bd)


if __name__ == "__main__":
    unittest.main()
