import json
import os
import tempfile
import unittest

import _pathsetup  # noqa: F401
import srcscore as S
import check_policy as CP


class CheckSchemaTests(unittest.TestCase):
    def test_real_policy_passes_schema_check(self):
        policy, problems = CP.check_schema(S.POLICY_PATH)
        self.assertEqual(problems, [])
        self.assertIsNotNone(policy)

    def test_missing_policy_file_reports_problem(self):
        policy, problems = CP.check_schema("/nonexistent/policy.json")
        self.assertIsNone(policy)
        self.assertTrue(problems)


class CheckModesTests(unittest.TestCase):
    def setUp(self):
        self.policy = S.load_policy()

    def test_real_mode_overlays_merge_and_validate(self):
        problems = CP.check_modes(self.policy, CP.MODES_DIR)
        self.assertEqual(problems, [])

    def test_missing_modes_dir_reports_problem(self):
        problems = CP.check_modes(self.policy, "/nonexistent/modes")
        self.assertTrue(problems)

    def test_overlay_that_breaks_validation_is_reported(self):
        with tempfile.TemporaryDirectory() as d:
            with open(os.path.join(d, "broken.json"), "w", encoding="utf-8") as f:
                json.dump({"defaults": {"field": "not-a-real-field"}}, f)
            problems = CP.check_modes(self.policy, d)
            self.assertTrue(any("field_halflife_years" in p for p in problems))

    def test_overlay_deep_merges_without_dropping_untouched_keys(self):
        overlay = S.load_mode_overlay("non-academic", CP.MODES_DIR)
        merged = S.apply_mode(self.policy, "non-academic", CP.MODES_DIR)
        self.assertEqual(merged["defaults"]["field"], "cs")

        untouched = ("tiers", "verdicts", "citations", "citation_gap",
                     "seo_path_patterns", "version_path_patterns",
                     "preprint_hosts", "field_halflife_years")
        for key in untouched:
            self.assertNotIn(key, overlay,
                             "%r is named by the overlay; pick a key it does "
                             "not touch" % key)
            self.assertEqual(merged[key], self.policy[key], key)

        self.assertEqual(set(merged["engagement"]), set(self.policy["engagement"]))
        self.assertEqual(merged["engagement"]["github"]["archived_penalty"],
                         overlay["engagement"]["github"]["archived_penalty"])


if __name__ == "__main__":
    unittest.main()
