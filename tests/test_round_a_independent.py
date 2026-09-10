"""Independent verification of Round A: domain_overrides, mode isolation,
community-opinion / official-docs ceilings, version_path penalty, hn_item.

Written from the specification, not from the implementing agent's tests.
Everything is offline (injected fixtures / NullCache) except one clearly
marked network sanity check that reports unavailability instead of skipping
silently.
"""

import copy
import json
import os
import re
import unittest

import _pathsetup  # noqa: F401
import srcscore as S
from srcscore_core import fetchers as F
from srcscore_core import identifiers as ID
from srcscore_core import policy as P
from srcscore_core import scoring as SC

MODES = ["academic", "non-academic", "community-opinion", "news", "official-docs"]


class NullCache:
    def get(self, k):
        return None

    def put(self, k, v):
        pass


def base_policy():
    return P.load_policy()


def merged(mode):
    return P.apply_mode(base_policy(), mode)


def score(url, policy, injected=None, field=None):
    return SC.score_one({"url": url}, policy, NullCache(),
                        field or policy["defaults"]["field"], False,
                        injected=injected)


def verdict_rank(policy, name):
    names = [b["name"] for b in policy["verdicts"]["bands"]]
    return names.index(name)


def at_least(policy, verdict, floor="SUPPORT"):
    """True when `verdict` is `floor` or better."""
    return verdict_rank(policy, verdict) <= verdict_rank(policy, floor)


# ---------------------------------------------------------------------------
# 1. domain_overrides
# ---------------------------------------------------------------------------

class DomainOverrideTests(unittest.TestCase):
    def test_every_mode_merges_to_a_valid_policy(self):
        for m in MODES:
            with self.subTest(mode=m):
                P.validate_policy(merged(m), m)  # raises on failure

    def test_no_domain_filed_in_two_tiers_after_merge(self):
        for m in MODES:
            seen = {}
            for tier, pats in merged(m)["domains"].items():
                for p in pats:
                    k = str(p).lower()
                    self.assertNotIn(k, seen,
                                     "%s: %r in %s and %s" % (m, p, seen.get(k), tier))
                    seen[k] = tier

    def test_overridden_domain_actually_lands_in_target_tier(self):
        for m in MODES:
            pol = merged(m)
            for pat, tier in (pol.get("domain_overrides") or {}).items():
                with self.subTest(mode=m, pat=pat):
                    self.assertIn(pat, pol["domains"][str(tier)])

    def test_caller_policy_is_not_mutated(self):
        base = base_policy()
        snapshot = copy.deepcopy(base)
        for m in MODES:
            P.apply_mode(base, m)
        self.assertEqual(base, snapshot)

    def test_apply_domain_overrides_does_not_mutate_input(self):
        pol = base_policy()
        pol = P.deep_merge(pol, {"domain_overrides": {"reddit.com": "1"}})
        snapshot = copy.deepcopy(pol)
        P.apply_domain_overrides(pol)
        self.assertEqual(pol, snapshot)

    def test_override_to_unknown_tier_raises(self):
        pol = P.deep_merge(base_policy(), {"domain_overrides": {"reddit.com": "99"}})
        with self.assertRaises(P.PolicyError):
            P.validate_policy(P.apply_domain_overrides(pol))

    def test_override_of_unregistered_domain_just_registers_it(self):
        pol = P.deep_merge(base_policy(),
                           {"domain_overrides": {"totally-unheard-of-xyz.example": "2"}})
        pol = P.apply_domain_overrides(pol)
        P.validate_policy(pol)
        self.assertIn("totally-unheard-of-xyz.example", pol["domains"]["2"])
        self.assertEqual(
            SC.match_tier("https://totally-unheard-of-xyz.example/a", pol)[0], "2")

    def test_match_tier_reflects_the_override(self):
        co = merged("community-opinion")
        self.assertEqual(SC.match_tier("https://www.reddit.com/r/rust/comments/x/", co)[0], "1")
        self.assertEqual(SC.match_tier("https://www.reddit.com/r/rust/comments/x/",
                                       base_policy())[0], "5")

    def test_case_insensitive_removal_from_old_tier(self):
        pol = P.deep_merge(base_policy(), {"domain_overrides": {"ReDdIt.CoM": "1"}})
        pol = P.apply_domain_overrides(pol)
        P.validate_policy(pol)
        self.assertNotIn("reddit.com", pol["domains"]["5"])


# ---------------------------------------------------------------------------
# 2. mode isolation
# ---------------------------------------------------------------------------

PROBE_URLS = [
    "https://www.reddit.com/r/rust/comments/abc/thing/",
    "https://news.ycombinator.com/item?id=12345678",
    "https://developer.mozilla.org/en-US/docs/Web/API/fetch",
    "https://docs.python.org/3/library/asyncio.html",
    "https://netflixtechblog.com/some-post",
    "https://eng.uber.com/some-post",
    "https://arxiv.org/abs/2401.00001",
    "https://nature.com/articles/x",
    "https://www.example-unregistered-xyz.test/page",
    "https://stackoverflow.com/questions/1/x",
    "https://kubernetes.io/docs/concepts/",
    "https://supabase.com/docs/guides/auth",
]


class ModeIsolationTests(unittest.TestCase):
    def test_academic_is_byte_identical_to_base(self):
        base, acad = base_policy(), merged("academic")
        for u in PROBE_URLS:
            with self.subTest(url=u):
                self.assertEqual(json.dumps(score(u, base), sort_keys=True),
                                 json.dumps(score(u, acad), sort_keys=True))

    def test_academic_merged_policy_has_no_overrides_or_floor(self):
        acad = merged("academic")
        self.assertFalse(acad.get("domain_overrides"))
        self.assertIsNone(acad.get("engagement_floor"))

    def test_community_overrides_do_not_leak_into_other_modes(self):
        for m in [x for x in MODES if x != "community-opinion"]:
            with self.subTest(mode=m):
                self.assertEqual(
                    SC.match_tier("https://www.reddit.com/r/x/comments/y/", merged(m))[0],
                    "5")

    def test_docs_overrides_do_not_leak_into_other_modes(self):
        for m in [x for x in MODES if x != "official-docs"]:
            with self.subTest(mode=m):
                t = SC.match_tier("https://developer.mozilla.org/en-US/docs/Web/API",
                                  merged(m))[0]
                self.assertNotEqual(t, "1", "%s promoted MDN to tier 1" % m)

    def test_engagement_floor_confined_to_community_opinion(self):
        for m in [x for x in MODES if x != "community-opinion"]:
            with self.subTest(mode=m):
                self.assertIsNone(merged(m).get("engagement_floor"))


# ---------------------------------------------------------------------------
# 3. community-opinion ceiling + engagement floor
# ---------------------------------------------------------------------------

class CommunityOpinionTests(unittest.TestCase):
    def setUp(self):
        self.pol = merged("community-opinion")

    def test_hot_hn_thread_reaches_support_or_better(self):
        r = score("https://news.ycombinator.com/item?id=38000000", self.pol,
                  injected={"hn": {"points": 900, "comments": 400}})
        self.assertTrue(at_least(self.pol, r["verdict"]),
                        "got %s (%s)" % (r["verdict"], r["score"]))
        self.assertNotIn("low-engagement", r["flags"])

    def test_dead_thread_does_not_reach_support(self):
        r = score("https://news.ycombinator.com/item?id=38000001", self.pol,
                  injected={"hn": {"points": 2, "comments": 0}})
        self.assertFalse(at_least(self.pol, r["verdict"]),
                         "dead thread reached %s (%s)" % (r["verdict"], r["score"]))
        self.assertIn("low-engagement", r["flags"])

    def test_an_unasked_lookup_is_not_scored_as_silence(self):
        """Revised after the round-A follow-up: "we could not ask" and "nobody
        was there" are different claims, and the pilot ran with HN returning 429
        on most lookups. With no lookup performed the floor must not fire."""
        r = score("https://www.reddit.com/r/rust/comments/x/y/", self.pol)
        self.assertNotIn("low-engagement", r["flags"])

    def test_a_successful_lookup_that_finds_nothing_is_silence(self):
        r = score("https://www.reddit.com/r/rust/comments/x/y/", self.pol,
                  injected={"hn": {"points": 0}})
        self.assertIn("low-engagement", r["flags"])
        self.assertFalse(at_least(self.pol, r["verdict"]))

    def test_floor_is_a_step_at_min_points(self):
        cfg = self.pol["engagement_floor"]
        n = int(cfg["min_points"])
        lo = score("https://news.ycombinator.com/item?id=1", self.pol,
                   injected={"hn": {"points": n - 1}})
        hi = score("https://news.ycombinator.com/item?id=2", self.pol,
                   injected={"hn": {"points": n}})
        self.assertIn("low-engagement", lo["flags"])
        self.assertNotIn("low-engagement", hi["flags"])
        self.assertAlmostEqual(hi["score"] - lo["score"],
                               -float(cfg["penalty"]) + (
                                   SC.engagement_points(self.pol, None, {"points": n})[0]
                                   - SC.engagement_points(self.pol, None, {"points": n - 1})[0]),
                               places=1)

    def test_floor_does_not_fire_for_unnamed_tiers(self):
        # quora is overridden to tier 4, lesswrong to tier 2: not in floor tiers.
        for u in ["https://www.quora.com/q/x", "https://www.lesswrong.com/posts/x"]:
            with self.subTest(url=u):
                r = score(u, self.pol, injected={"hn": {"points": 0}})
                self.assertNotIn("low-engagement", r["flags"])
        r = score("https://www.example-unregistered-xyz.test/p", self.pol)
        self.assertNotIn("low-engagement", r["flags"])

    def test_floor_helper_inert_when_engagement_signal_off(self):
        docs = merged("official-docs")
        self.assertFalse(P.signal_enabled(docs, "engagement"))
        # even if a floor were present, score_one must not apply it
        pol = P.deep_merge(docs, {"engagement_floor": {
            "min_points": 10, "penalty": -32.0, "flag": "low-engagement",
            "tiers": ["1"]}})
        P.validate_policy(pol)
        r = score("https://developer.mozilla.org/en-US/docs/Web/API", pol,
                  injected={"hn": {"points": 0}})
        self.assertNotIn("low-engagement", r["flags"])

    def test_trusted_person_bonus_still_applies(self):
        r = score("https://x.com/karpathy/status/123", self.pol,
                  injected={"hn": {"points": 50}})
        self.assertTrue(any(n.startswith("trusted:") for n in r["signals"]))


# ---------------------------------------------------------------------------
# 4. official-docs ceiling
# ---------------------------------------------------------------------------

DOC_URLS = [
    "https://developer.mozilla.org/en-US/docs/Web/API/fetch",
    "https://docs.python.org/3/library/asyncio.html",
    "https://kubernetes.io/docs/concepts/overview/",
    "https://supabase.com/docs/guides/auth",
]

BLOG_URLS = ["https://netflixtechblog.com/a-post", "https://eng.uber.com/a-post"]


class OfficialDocsTests(unittest.TestCase):
    def setUp(self):
        self.pol = merged("official-docs")

    def test_docs_reach_support_or_better(self):
        for u in DOC_URLS:
            with self.subTest(url=u):
                r = score(u, self.pol)
                self.assertTrue(at_least(self.pol, r["verdict"]),
                                "%s -> %s (%s)" % (u, r["verdict"], r["score"]))

    def test_vendor_engineering_blogs_stay_tier_3(self):
        for u in BLOG_URLS:
            with self.subTest(url=u):
                self.assertEqual(SC.match_tier(u, self.pol)[0], "3")

    def test_all_signals_off_so_score_equals_tier_base(self):
        r = score("https://kubernetes.io/docs/concepts/overview/", self.pol)
        self.assertAlmostEqual(r["score"], P.tier_base(self.pol, "1"), places=1)


# ---------------------------------------------------------------------------
# 5. version_path penalty
# ---------------------------------------------------------------------------

class VersionPathTests(unittest.TestCase):
    def setUp(self):
        self.pol = merged("official-docs")

    def test_superseded_nextjs_docs_flagged_and_lower(self):
        stale = score("https://nextjs.org/docs/13/pages/api-reference/next-config-js",
                      self.pol)
        cur = score("https://nextjs.org/docs/app/api-reference/next-config-js", self.pol)
        self.assertIn("stale-version", stale["flags"])
        self.assertNotIn("stale-version", cur["flags"])
        self.assertLess(stale["score"], cur["score"])

    def test_stripe_versioned_api_path_not_flagged(self):
        r = score("https://docs.stripe.com/api/v1/charges", self.pol)
        self.assertNotIn("stale-version", r["flags"])

    def test_literal_markers_flagged(self):
        for u in ["https://kubernetes.io/docs/legacy/x", "https://helm.sh/docs/deprecated/y",
                  "https://go.dev/doc/archive/old"]:
            with self.subTest(url=u):
                self.assertIn("stale-version", score(u, self.pol)["flags"])

    def test_penalty_magnitude_matches_policy(self):
        cfg = self.pol["penalties"]["version_path"]
        pts, flag = SC.version_penalty(self.pol, "https://nextjs.org/docs/13/x")
        self.assertEqual(flag, "stale-version")
        self.assertLess(pts, 0)
        lit_pts, lit_flag = SC.path_penalty(
            "https://example.com/legacy/x",
            self.pol.get("version_path_patterns"), cfg)
        self.assertEqual((lit_pts, lit_flag), (float(cfg["points"]), "stale-version"))
        self.assertEqual(pts, float(cfg["points"]))
        self.assertEqual(flag, "stale-version")
        self.assertEqual(float(cfg["points"]), -28.0)

    def test_path_penalty_helper_shared_with_seo(self):
        for pol in (self.pol, P.load_policy()):
            cfg = pol["penalties"]["seo_path"]
            pattern = str(pol["seo_path_patterns"][0]).strip("/")
            pts, flag = SC.path_penalty("https://example.com/%s/page" % pattern,
                                        pol["seo_path_patterns"], cfg)
            with self.subTest(flag=cfg.get("flag")):
                self.assertEqual(flag, cfg["flag"])
                self.assertEqual(pts, float(cfg["points"]))
                self.assertLess(pts, 0)
        self.assertEqual(P.load_policy()["penalties"]["seo_path"]["flag"], "seo-path")

    def test_no_false_positives_on_current_docs(self):
        """Asserted through `version_penalty`, not the raw regex: after the
        round-A follow-up the regex captures any version segment and the
        decision (bare major vs. precise release vs. a pinned --doc-version)
        lives in the function."""
        current = [
            "https://docs.python.org/3/library/os.html",
            "https://docs.python.org/3.12/library/os.html",
            "https://kubernetes.io/docs/reference/kubernetes-api/",
            "https://docs.djangoproject.com/en/5.0/ref/models/",
            "https://docs.oracle.com/en/java/javase/21/docs/api/",
            "https://prometheus.io/docs/prometheus/2.53/querying/basics/",
            "https://grafana.com/docs/grafana/v11.0/",
            "https://docs.gitlab.com/17.3/ee/user/",
            "https://swagger.io/docs/specification/v3_0/about/",
            "https://hexdocs.pm/phoenix/1.7.0/overview.html",
            "https://numpy.org/doc/2.1/user/absolute_beginners.html",
        ]
        hits = [u for u in current
                if SC.version_penalty(self.pol, u)[1] is not None]
        self.assertEqual(hits, [], "flags current-docs URLs: %s" % hits)

    def test_a_pinned_version_makes_every_other_version_stale(self):
        pol = dict(self.pol, doc_versions={"nextjs.org": "14"})
        self.assertIsNone(SC.version_penalty(pol, "https://nextjs.org/docs/14/x")[1])
        self.assertIsNone(SC.version_penalty(pol, "https://nextjs.org/docs/app/x")[1])
        for url in ("https://nextjs.org/docs/13/x", "https://nextjs.org/docs/15/x"):
            self.assertEqual(SC.version_penalty(pol, url)[1], "stale-version", url)
        # the pin is scoped to its own domain
        self.assertIsNone(
            SC.version_penalty(pol, "https://numpy.org/doc/2.1/user/x.html")[1])

    def test_query_and_fragment_are_not_read_as_path(self):
        for url in ("https://example.com/?next=/docs/13/x",
                    "https://example.com/a#/legacy/b"):
            r = score(url, self.pol)
            self.assertNotIn("stale-version", r["flags"], url)


# ---------------------------------------------------------------------------
# 6. hn_item / extract_hn_item_id
# ---------------------------------------------------------------------------

class HnItemIdTests(unittest.TestCase):
    def test_plain_permalink(self):
        self.assertEqual(
            ID.extract_hn_item_id("https://news.ycombinator.com/item?id=38000000"),
            "38000000")

    def test_extra_query_params(self):
        for u in ["https://news.ycombinator.com/item?id=123&p=2",
                  "https://news.ycombinator.com/item?p=2&id=123",
                  "http://news.ycombinator.com/item?id=123#c1"]:
            with self.subTest(url=u):
                self.assertEqual(ID.extract_hn_item_id(u), "123")

    def test_non_hn_urls_do_not_match(self):
        for u in ["https://example.com/item?id=123",
                  "https://reddit.com/r/x/comments/123",
                  "https://news.ycombinator.com/",
                  "https://news.ycombinator.com/newest",
                  "https://hn.algolia.com/?query=item?id=1"]:
            with self.subTest(url=u):
                self.assertIsNone(ID.extract_hn_item_id(u))


class _StubHttp:
    def __init__(self, mapping):
        self.mapping = mapping
        self.calls = []

    def __call__(self, url, timeout):
        self.calls.append(url)
        return self.mapping.get(url, None)


class HnItemFetchTests(unittest.TestCase):
    def setUp(self):
        self.orig = F.http_json

    def tearDown(self):
        F.http_json = self.orig

    def test_hn_item_hits_items_endpoint_and_maps_fields(self):
        stub = _StubHttp({"https://hn.algolia.com/api/v1/items/999":
                          {"points": 412, "children": [{}, {}, {}]}})
        F.http_json = stub
        out = F.hn_item("999", NullCache(), 5)
        self.assertEqual(out, {"points": 412, "comments": 3})
        self.assertEqual(stub.calls, ["https://hn.algolia.com/api/v1/items/999"])

    def test_hn_item_zero_points_is_none(self):
        F.http_json = _StubHttp({"https://hn.algolia.com/api/v1/items/1":
                                 {"points": 0, "children": []}})
        self.assertIsNone(F.hn_item("1", NullCache(), 5))

    def test_hn_item_propagates_fetch_failed(self):
        F.http_json = lambda u, t: F.FETCH_FAILED
        self.assertIs(F.hn_item("1", NullCache(), 5), F.FETCH_FAILED)

    def test_hn_item_cache_key_distinct_from_hn_points(self):
        seen = {}

        class C:
            def get(self, k):
                return seen.get(k)

            def put(self, k, v):
                seen[k] = v

        F.http_json = _StubHttp({
            "https://hn.algolia.com/api/v1/items/7": {"points": 5, "children": []}})
        c = C()
        F.hn_item("7", c, 5)
        self.assertIn("hnitem:7", seen)
        self.assertNotIn("hn:7", seen)

    def test_score_one_routes_permalink_through_hn_item(self):
        """With use_net on, an HN permalink must be looked up by id, not by
        the (never-matching) article-url search."""
        calls = []
        F.http_json = lambda u, t: (calls.append(u) or
                                    ({"points": 500, "children": []}
                                     if "/items/" in u else None))
        pol = merged("community-opinion")
        r = SC.score_one({"url": "https://news.ycombinator.com/item?id=38000000"},
                         pol, NullCache(), pol["defaults"]["field"], True)
        self.assertTrue(any("/items/38000000" in u for u in calls), calls)
        self.assertFalse(any("/search?" in u for u in calls), calls)
        self.assertTrue(at_least(pol, r["verdict"]), r)

    def test_network_sanity_real_hn_item(self):
        """One real call. Reports unavailability rather than skipping silently."""
        try:
            out = self.orig("https://hn.algolia.com/api/v1/items/8863", 8)
        except Exception as e:  # pragma: no cover
            self.fail("NETWORK UNAVAILABLE: %s" % e)
        if out is F.FETCH_FAILED or out is None:
            self.fail("NETWORK UNAVAILABLE: HN Algolia items endpoint unreachable")
        self.assertGreater(out.get("points") or 0, 0)


# ---------------------------------------------------------------------------
# 7. unregistered domains stay untrusted
# ---------------------------------------------------------------------------

class UnregisteredDomainTests(unittest.TestCase):
    URL = "https://some-random-unregistered-blog-4711.test/post/how-i-did-it"

    def test_default_tier_and_base_in_every_mode(self):
        for m in MODES:
            pol = merged(m)
            with self.subTest(mode=m):
                tier, pat = SC.match_tier(self.URL, pol)
                self.assertEqual(tier, "5")
                self.assertIsNone(pat)
                self.assertEqual(P.tier_base(pol, "5"), 32)

    def test_never_reaches_support_even_with_max_engagement(self):
        for m in MODES:
            pol = merged(m)
            with self.subTest(mode=m):
                r = score(self.URL, pol, injected={"hn": {"points": 100000}})
                self.assertFalse(at_least(pol, r["verdict"]),
                                 "%s: %s (%s)" % (m, r["verdict"], r["score"]))

    def test_unregistered_subdomain_of_registered_domain_still_inherits(self):
        # sanity: host_matches is subdomain-aware, so this is NOT unregistered
        self.assertEqual(SC.match_tier("https://blog.nature.com/x", base_policy())[0], "1")


if __name__ == "__main__":
    unittest.main()
