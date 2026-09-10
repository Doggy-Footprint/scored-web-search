"""Independent verification of the mode-overlay functionality added on top
of the srcscore_core split: deep_merge, apply_mode, and the --mode wiring
in srcscore_core.cli.main.

Run offline only (NullCache / --no-net / injected fixtures), same convention
as the rest of tests/.
"""

import copy
import io
import json
import os
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout

import _pathsetup  # noqa: F401
import srcscore as S
import check_policy as CP
from srcscore_core import cli as CLI
from srcscore_core import scoring as SC


# ----------------------------------------------------------------------------
# deep_merge
# ----------------------------------------------------------------------------

class DeepMergeTests(unittest.TestCase):
    def test_nested_dict_keys_merge_recursively(self):
        base = {"a": {"x": 1, "y": 2}, "b": 5}
        overlay = {"a": {"y": 99}}
        out = S.deep_merge(base, overlay)
        self.assertEqual(out, {"a": {"x": 1, "y": 99}, "b": 5})

    def test_overlay_completely_replaces_a_list(self):
        base = {"items": [1, 2, 3], "b": 1}
        overlay = {"items": [9]}
        out = S.deep_merge(base, overlay)
        self.assertEqual(out["items"], [9])

    def test_overlay_wins_on_scalar_conflict(self):
        base = {"n": 1, "s": "old"}
        overlay = {"n": 2, "s": "new"}
        out = S.deep_merge(base, overlay)
        self.assertEqual(out, {"n": 2, "s": "new"})

    def test_keys_overlay_does_not_mention_are_untouched(self):
        base = {"a": 1, "b": {"c": 2, "d": 3}, "e": [1, 2]}
        overlay = {"b": {"c": 20}}
        out = S.deep_merge(base, overlay)
        self.assertEqual(out["a"], 1)
        self.assertEqual(out["b"]["d"], 3)
        self.assertEqual(out["e"], [1, 2])

    def test_base_dict_is_not_mutated(self):
        base = {"a": {"x": 1}}
        base_copy = copy.deepcopy(base)
        S.deep_merge(base, {"a": {"x": 2}})
        self.assertEqual(base, base_copy)

    def test_readme_key_in_overlay_is_ignored(self):
        base = {"a": 1}
        overlay = {"_readme": "explains this overlay", "a": 2}
        out = S.deep_merge(base, overlay)
        self.assertNotIn("_readme", out)
        self.assertEqual(out["a"], 2)


# ----------------------------------------------------------------------------
# apply_mode against the five real overlay files
# ----------------------------------------------------------------------------

class ApplyModeRealFilesTests(unittest.TestCase):
    def setUp(self):
        self.policy = S.load_policy()

    def test_all_five_modes_load_merge_and_validate(self):
        for mode in ("academic", "non-academic", "community-opinion", "news", "official-docs"):
            with self.subTest(mode=mode):
                merged = S.apply_mode(self.policy, mode)
                S.validate_policy(merged, "mode:%s" % mode)

    def test_academic_mode_is_a_true_no_op(self):
        merged = S.apply_mode(self.policy, "academic")
        self.assertEqual(merged, self.policy)

    def test_official_docs_disables_all_three_signals(self):
        merged = S.apply_mode(self.policy, "official-docs")
        for sig in ("recency_decay", "peer_review", "engagement"):
            self.assertFalse(S.signal_enabled(merged, sig))
        # the tier *bases* are untouched; the domains are deliberately re-filed
        # (see domain_overrides in modes/official_docs.json)
        self.assertEqual(merged["tiers"], self.policy["tiers"])
        self.assertNotEqual(merged["domains"], self.policy["domains"])
        # every domain in the merged table is still filed exactly once
        flat = [d for pats in merged["domains"].values() for d in pats]
        self.assertEqual(len(flat), len(set(flat)))

    def test_news_mode_overrides_recency_but_keeps_engagement_hn_min_tier(self):
        merged = S.apply_mode(self.policy, "news")
        self.assertEqual(merged["recency"]["decay"]["grace_years"], 0.01)
        self.assertFalse(S.signal_enabled(merged, "peer_review"))
        self.assertTrue(S.signal_enabled(merged, "engagement"))
        self.assertEqual(merged["defaults"]["field"], "news")
        self.assertIn("news", merged["field_halflife_years"])
        # base ai/cs/... half-lives survive the merge
        self.assertEqual(merged["field_halflife_years"]["ai"],
                          self.policy["field_halflife_years"]["ai"])

    def test_community_opinion_sets_new_default_field_and_halflife(self):
        merged = S.apply_mode(self.policy, "community-opinion")
        self.assertEqual(merged["defaults"]["field"], "opinion")
        self.assertEqual(merged["field_halflife_years"]["opinion"], 0.75)
        self.assertFalse(S.signal_enabled(merged, "peer_review"))

    def test_community_opinion_carries_a_trusted_people_list(self):
        merged = S.apply_mode(self.policy, "community-opinion")
        self.assertIn("trusted_people", merged)
        self.assertIn("x.com", merged["trusted_people"]["hosts"])
        self.assertGreater(len(merged["trusted_people"]["hosts"]["x.com"]), 0)

    def test_non_academic_keeps_peer_review_and_recency_on(self):
        merged = S.apply_mode(self.policy, "non-academic")
        self.assertTrue(S.signal_enabled(merged, "peer_review"))
        self.assertTrue(S.signal_enabled(merged, "recency_decay"))
        self.assertEqual(merged["defaults"]["field"], "cs")


# ----------------------------------------------------------------------------
# CLI-level tests
# ----------------------------------------------------------------------------

class CliTests(unittest.TestCase):
    def test_unknown_mode_choice_rejected_by_argparse(self):
        buf = io.StringIO()
        with redirect_stderr(buf):
            with self.assertRaises(SystemExit) as cm:
                CLI.main(["--mode", "not-a-real-mode", "--no-net", "-u", "https://example.com"])
        self.assertEqual(cm.exception.code, 2)
        self.assertIn("invalid choice", buf.getvalue())

    def test_mode_flag_selects_overlay_end_to_end(self):
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            rc = CLI.main([
                "--mode", "non-academic", "--no-net",
                "-u", "https://arxiv.org/abs/2201.11111",
                "--format", "json",
            ])
        self.assertEqual(rc, 0, msg=err.getvalue())
        rows = json.loads(out.getvalue())
        self.assertEqual(len(rows), 1)
        self.assertIn("score", rows[0])


# ----------------------------------------------------------------------------
# check_policy.check_modes
# ----------------------------------------------------------------------------

class CheckModesBrokenOverlayTests(unittest.TestCase):
    def setUp(self):
        self.policy = S.load_policy()

    def test_real_five_modes_pass_with_zero_problems(self):
        problems = CP.check_modes(self.policy, CP.MODES_DIR)
        self.assertEqual(problems, [])

    def test_overlay_setting_defaults_field_to_unknown_field_is_caught(self):
        with tempfile.TemporaryDirectory() as d:
            with open(os.path.join(d, "broken_field.json"), "w", encoding="utf-8") as f:
                json.dump({"defaults": {"field": "totally-bogus-field"}}, f)
            problems = CP.check_modes(self.policy, d)
        self.assertTrue(problems)
        self.assertTrue(any("field_halflife_years" in p for p in problems))

    def test_overlay_breaking_verdict_band_ordering_is_caught(self):
        with tempfile.TemporaryDirectory() as d:
            with open(os.path.join(d, "broken_bands.json"), "w", encoding="utf-8") as f:
                json.dump({"verdicts": {"bands": [{"name": "X", "min": 10}]}}, f)
            problems = CP.check_modes(self.policy, d)
        self.assertTrue(problems)
        self.assertTrue(any("lowest verdict band" in p for p in problems))


# ----------------------------------------------------------------------------
# Round A: a mode owns its own tier reading (domain_overrides), and the two
# ceilings the n=5 pilot found are gone. These assert reachability: what the
# mode must be *able* to produce.
# ----------------------------------------------------------------------------

def _score(policy, url, field, inject=None):
    return SC.score_one({"url": url}, policy, None, field, False, inject or {})


class CommunityOpinionCeilingTests(unittest.TestCase):
    """PILOT F2: a discussion thread topped out at 50 against a SUPPORT
    threshold of 62, so the mode could not pass its own material."""

    def setUp(self):
        self.policy = S.apply_mode(S.load_policy(), "community-opinion")
        S.validate_policy(self.policy, "mode:community-opinion")

    def test_high_engagement_hn_thread_reaches_support(self):
        r = _score(self.policy, "https://news.ycombinator.com/item?id=42",
                   "opinion", {"hn": {"points": 400}})
        self.assertIn(r["verdict"], ("SUPPORT", "PRIMARY"))

    def test_busy_reddit_thread_reaches_support(self):
        r = _score(self.policy, "https://www.reddit.com/r/rust/comments/a/b/",
                   "opinion", {"hn": {"points": 150}})
        self.assertIn(r["verdict"], ("SUPPORT", "PRIMARY"))

    def test_the_same_thread_without_engagement_does_not_pass(self):
        """The floor is the other half of the promotion: tier 1 says the venue
        is where the answer lives, engagement says whether anyone was there."""
        r = _score(self.policy, "https://news.ycombinator.com/item?id=43",
                   "opinion", {"hn": {"points": 1}})
        self.assertIn("low-engagement", r["flags"])
        self.assertNotIn(r["verdict"], ("SUPPORT", "PRIMARY"))

    def test_engagement_floor_only_touches_the_tiers_the_mode_names(self):
        r = _score(self.policy, "https://arxiv.org/abs/2501.00001", "opinion",
                   {"scholar": {"citations": 40, "year": 2025, "age_years": 1.0,
                                "peer_reviewed": False}})
        self.assertNotIn("low-engagement", r["flags"])


class OfficialDocsCeilingTests(unittest.TestCase):
    """PILOT F3: with every signal off, score == tier base, and documentation
    sat at tier 3 (60) under a SUPPORT threshold of 62 -- nothing could pass,
    and registering more domains would not have changed that."""

    def setUp(self):
        self.policy = S.apply_mode(S.load_policy(), "official-docs")
        S.validate_policy(self.policy, "mode:official-docs")

    def test_canonical_docs_are_citable(self):
        for url in ("https://developer.mozilla.org/en-US/docs/Web/API/fetch",
                    "https://docs.python.org/3/library/asyncio.html",
                    "https://kubernetes.io/docs/concepts/",
                    "https://supabase.com/docs/guides/auth"):
            with self.subTest(url=url):
                self.assertIn(_score(self.policy, url, "cs")["verdict"],
                              ("SUPPORT", "PRIMARY"))

    def test_superseded_version_ranks_below_the_current_page(self):
        cur = _score(self.policy, "https://nextjs.org/docs/app/routing", "cs")
        old = _score(self.policy, "https://nextjs.org/docs/13/pages/routing", "cs")
        self.assertIn("stale-version", old["flags"])
        self.assertNotIn("stale-version", cur["flags"])
        self.assertLess(old["score"], cur["score"])

    def test_a_current_versioned_api_path_is_not_read_as_stale(self):
        r = _score(self.policy, "https://docs.stripe.com/api/v1/charges", "cs")
        self.assertNotIn("stale-version", r["flags"])

    def test_vendor_engineering_blogs_are_not_promoted_to_documentation(self):
        r = _score(self.policy, "https://netflixtechblog.com/some-post", "cs")
        self.assertEqual(r["tier"], "3")


class ModeIsolationTests(unittest.TestCase):
    """An override belongs to the mode that declares it. If it leaked into the
    base policy the academic profile would silently inherit another mode's
    world-model, which is the failure this whole design is meant to avoid."""

    def setUp(self):
        self.base = S.load_policy()

    def test_academic_mode_is_unaffected_by_other_modes_overrides(self):
        academic = S.apply_mode(self.base, "academic")
        for url in ("https://news.ycombinator.com/item?id=1",
                    "https://www.reddit.com/r/x/comments/a/b/",
                    "https://developer.mozilla.org/en-US/docs/Web/API/fetch"):
            with self.subTest(url=url):
                self.assertEqual(_score(academic, url, "ai")["tier"],
                                 _score(self.base, url, "ai")["tier"])

    def test_applying_one_mode_does_not_mutate_the_policy_it_was_given(self):
        before = copy.deepcopy(self.base["domains"])
        S.apply_mode(self.base, "official-docs")
        S.apply_mode(self.base, "community-opinion")
        self.assertEqual(self.base["domains"], before)

    def test_every_mode_keeps_each_domain_filed_exactly_once(self):
        for mode in ("academic", "non-academic", "community-opinion",
                     "news", "official-docs"):
            with self.subTest(mode=mode):
                merged = S.apply_mode(self.base, mode)
                flat = [str(d).lower() for pats in merged["domains"].values()
                        for d in pats]
                self.assertEqual(len(flat), len(set(flat)))

    def test_unregistered_domains_stay_untrusted_in_every_mode(self):
        """Round A decision: unknown is not a score the policy earned its way
        to, it is the absence of a trace. It stays at the default tier."""
        for mode, field in (("academic", "ai"), ("non-academic", "cs"),
                            ("community-opinion", "opinion"), ("news", "news"),
                            ("official-docs", "cs")):
            with self.subTest(mode=mode):
                merged = S.apply_mode(self.base, mode)
                r = _score(merged, "https://never-heard-of-it.example/post", field)
                self.assertEqual(r["tier"], "5")
                self.assertNotIn(r["verdict"], ("SUPPORT", "PRIMARY"))


if __name__ == "__main__":
    unittest.main()
