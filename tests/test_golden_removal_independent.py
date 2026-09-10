"""Independent test suite verifying the complete and clean removal of golden
fixture files, logic, tests, and CI hooks from the repository.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest

TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
if TESTS_DIR not in sys.path:
    sys.path.insert(0, TESTS_DIR)

import _pathsetup  # noqa: F401
import check_policy as CP

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPTS_DIR = os.path.join(ROOT, "scripts")
CP_PATH = os.path.join(SCRIPTS_DIR, "check_policy.py")
HOOK_PATH = os.path.join(ROOT, "hooks", "pre-commit")
TEST_CP_PATH = os.path.join(ROOT, "tests", "test_check_policy.py")


class TestGoldenRemovalIndependent(unittest.TestCase):
    """Verifies that all traces of golden files, functions, and tests are gone."""

    def test_scripts_golden_dir_does_not_exist(self):
        """scripts/golden/ must not exist on disk."""
        golden_dir = os.path.join(SCRIPTS_DIR, "golden")
        self.assertFalse(
            os.path.exists(golden_dir),
            f"scripts/golden directory still exists at {golden_dir}",
        )

    def test_check_policy_has_no_golden_attributes_or_functions(self):
        """check_policy.py module must not define or export golden attributes/functions."""
        forbidden_attrs = [
            "GOLDEN_DIR",
            "GOLDEN_PATH",
            "run_golden",
            "check_golden",
            "check_verdict_coverage",
            "iter_golden_files",
            "mode_for_golden_file",
        ]
        for attr in forbidden_attrs:
            self.assertFalse(
                hasattr(CP, attr),
                f"check_policy still has forbidden attribute/function: {attr}",
            )

    def test_check_policy_source_has_no_golden_references(self):
        """check_policy.py source code must contain no golden references or flags."""
        with open(CP_PATH, "r", encoding="utf-8") as f:
            content = f.read()

        forbidden_patterns = [
            "golden",
            "GOLDEN_DIR",
            "GOLDEN_PATH",
            "run_golden",
            "check_golden",
            "check_verdict_coverage",
            "iter_golden_files",
            "--golden-dir",
            "--bless",
        ]
        for pat in forbidden_patterns:
            self.assertNotIn(
                pat.lower(),
                content.lower(),
                f"check_policy.py still contains reference to {pat!r}",
            )

    def test_check_policy_cli_default_succeeds(self):
        """check_policy.py runs with exit code 0 by default."""
        res = subprocess.run(
            [sys.executable, CP_PATH],
            capture_output=True,
            text=True,
            cwd=ROOT,
        )
        self.assertEqual(
            res.returncode,
            0,
            f"check_policy.py failed with exit code {res.returncode}:\nstdout: {res.stdout}\nstderr: {res.stderr}",
        )
        self.assertIn("check_policy: ok", res.stdout)
        self.assertNotIn("golden", res.stdout.lower())

    def test_check_policy_cli_only_schema_succeeds(self):
        """check_policy.py --only schema runs with exit code 0."""
        res = subprocess.run(
            [sys.executable, CP_PATH, "--only", "schema"],
            capture_output=True,
            text=True,
            cwd=ROOT,
        )
        self.assertEqual(
            res.returncode,
            0,
            f"check_policy.py --only schema failed:\nstdout: {res.stdout}\nstderr: {res.stderr}",
        )
        self.assertIn("check_policy: ok", res.stdout)

    def test_check_policy_cli_only_modes_succeeds(self):
        """check_policy.py --only modes runs with exit code 0."""
        res = subprocess.run(
            [sys.executable, CP_PATH, "--only", "modes"],
            capture_output=True,
            text=True,
            cwd=ROOT,
        )
        self.assertEqual(
            res.returncode,
            0,
            f"check_policy.py --only modes failed:\nstdout: {res.stdout}\nstderr: {res.stderr}",
        )
        self.assertIn("check_policy: ok", res.stdout)

    def test_check_policy_cli_invalid_schema_fails(self):
        """check_policy.py exits non-zero if invalid schema or missing policy is given."""
        # Non-existent policy file
        res = subprocess.run(
            [sys.executable, CP_PATH, "--policy", "/nonexistent/policy.json"],
            capture_output=True,
            text=True,
            cwd=ROOT,
        )
        self.assertNotEqual(res.returncode, 0)
        self.assertIn("schema:", res.stderr)

        # Invalid/corrupted policy JSON
        with tempfile.TemporaryDirectory() as d:
            bad_policy = os.path.join(d, "bad_policy.json")
            with open(bad_policy, "w", encoding="utf-8") as f:
                json.dump({"not_a_valid_policy": True}, f)
            res = subprocess.run(
                [sys.executable, CP_PATH, "--policy", bad_policy],
                capture_output=True,
                text=True,
                cwd=ROOT,
            )
            self.assertNotEqual(res.returncode, 0)
            self.assertIn("schema:", res.stderr)

    def test_check_policy_cli_broken_mode_fails(self):
        """check_policy.py exits non-zero if broken mode overlay or missing modes-dir is given."""
        # Non-existent modes directory
        res = subprocess.run(
            [sys.executable, CP_PATH, "--modes-dir", "/nonexistent/modes"],
            capture_output=True,
            text=True,
            cwd=ROOT,
        )
        self.assertNotEqual(res.returncode, 0)
        self.assertIn("modes:", res.stderr)

        # Broken mode overlay file
        with tempfile.TemporaryDirectory() as d:
            broken_mode = os.path.join(d, "broken.json")
            with open(broken_mode, "w", encoding="utf-8") as f:
                json.dump({"defaults": {"field": "not-a-real-field"}}, f)
            res = subprocess.run(
                [sys.executable, CP_PATH, "--modes-dir", d],
                capture_output=True,
                text=True,
                cwd=ROOT,
            )
            self.assertNotEqual(res.returncode, 0)
            self.assertIn("modes:", res.stderr)

    def test_hooks_pre_commit_runs_with_exit_code_0(self):
        """hooks/pre-commit runs cleanly with exit code 0."""
        res = subprocess.run(
            ["sh", HOOK_PATH],
            capture_output=True,
            text=True,
            cwd=ROOT,
        )
        self.assertEqual(
            res.returncode,
            0,
            f"hooks/pre-commit failed with exit code {res.returncode}:\nstdout: {res.stdout}\nstderr: {res.stderr}",
        )

    def test_hooks_pre_commit_has_no_golden_references(self):
        """hooks/pre-commit must not contain golden references or --bless."""
        with open(HOOK_PATH, "r", encoding="utf-8") as f:
            content = f.read()

        self.assertNotIn("golden", content.lower())
        self.assertNotIn("--bless", content)
        self.assertNotIn("scripts/golden", content)

    def test_test_check_policy_runs_with_exit_code_0(self):
        """tests/test_check_policy.py runs and passes with exit code 0."""
        env = dict(os.environ)
        env["PYTHONPATH"] = f"{TESTS_DIR}:{ROOT}:{env.get('PYTHONPATH', '')}".rstrip(":")
        res = subprocess.run(
            [sys.executable, "-m", "unittest", "tests/test_check_policy.py"],
            capture_output=True,
            text=True,
            cwd=ROOT,
            env=env,
        )
        self.assertEqual(
            res.returncode,
            0,
            f"tests/test_check_policy.py failed:\nstdout: {res.stdout}\nstderr: {res.stderr}",
        )
        self.assertIn("OK", res.stderr)

    def test_test_check_policy_has_no_golden_references(self):
        """tests/test_check_policy.py must contain no CheckGoldenTests, SAMPLE_GOLDEN, or load_golden."""
        with open(TEST_CP_PATH, "r", encoding="utf-8") as f:
            content = f.read()

        forbidden_symbols = [
            "CheckGoldenTests",
            "SAMPLE_GOLDEN",
            "load_golden",
            "golden",
        ]
        for sym in forbidden_symbols:
            self.assertNotIn(
                sym.lower(),
                content.lower(),
                f"tests/test_check_policy.py still contains {sym!r}",
            )


if __name__ == "__main__":
    unittest.main()
