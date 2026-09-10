import copy
import io
import json
import unittest
from contextlib import redirect_stdout, redirect_stderr

import _pathsetup  # noqa: F401
import srcscore as S
from srcscore_core import cli as CLI
from srcscore_core import policy as P
from srcscore_core import scoring as SC

MODES = ["academic", "non-academic", "community-opinion", "news", "official-docs"]
OTHER_MODES = [m for m in MODES if m != "news"]

# One representative URL per rung of the ladder modes/news.json describes.
NEWS_LADDER = [
    ("1", "https://www.federalregister.gov/documents/2026/01/02/x"),
    ("2", "https://www.reuters.com/technology/some-story"),
    ("3", "https://www.gibsondunn.com/some-regulatory-update/"),
    ("4", "https://arstechnica.com/tech-policy/2026/01/some-story/"),
    ("5", "https://never-heard-of-this-outlet.example/story/1"),
]


class NullCache:
    def get(self, k):
        return None

    def put(self, k, v):
        pass


def base_policy():
    return P.load_policy()


def merged(mode, policy=None):
    return P.apply_mode(policy or base_policy(), mode)


def score(policy, url, field=None, injected=None):
    return SC.score_one({"url": url}, policy, NullCache(),
                        field or policy["defaults"]["field"], False,
                        injected or {})


def band_min(policy, name):
    for b in policy["verdicts"]["bands"]:
        if b["name"] == name:
            return float(b["min"])
    raise AssertionError("no verdict band named %r" % name)


def flat_domains(policy):
    return [str(d).lower() for pats in policy["domains"].values() for d in pats]


def domain_index(policy):
    """domain -> tier name, for the merged domains table."""
    out = {}
    for tier, pats in policy["domains"].items():
        for d in pats:
            out[str(d).lower()] = tier
    return out


# ---------------------------------------------------------------------------
# Claim 1: the `tiers` re-base belongs to news and to nothing else
# ---------------------------------------------------------------------------

class TierOverlayIsolationTests(unittest.TestCase):
    def setUp(self):
        self.base = base_policy()

    def test_news_is_the_only_mode_that_rebases_tier_three(self):
        self.assertEqual(merged("news", self.base)["tiers"]["3"]["base"], 66)
        for mode in OTHER_MODES:
            with self.subTest(mode=mode):
                self.assertEqual(merged(mode, self.base)["tiers"]["3"]["base"], 60)

    def test_base_policy_file_still_says_sixty(self):
        """The overlay must not have been "fixed" by editing the base table -
        that would move the academic profile too."""
        self.assertEqual(self.base["tiers"]["3"]["base"], 60)

    def test_every_other_mode_leaves_the_whole_tier_table_untouched(self):
        for mode in OTHER_MODES:
            with self.subTest(mode=mode):
                self.assertEqual(merged(mode, self.base)["tiers"], self.base["tiers"])

    def test_news_changes_tier_three_only(self):
        m = merged("news", self.base)
        self.assertEqual(set(m["tiers"]), set(self.base["tiers"]))
        for name in self.base["tiers"]:
            if name == "3":
                continue
            with self.subTest(tier=name):
                self.assertEqual(m["tiers"][name], self.base["tiers"][name])
        self.assertNotEqual(m["tiers"]["3"]["label"], self.base["tiers"]["3"]["label"])

    def test_applying_news_does_not_mutate_the_policy_it_was_given(self):
        snapshot = copy.deepcopy(self.base)
        merged("news", self.base)
        merged("news", self.base)
        self.assertEqual(self.base, snapshot)

    def test_a_tier_three_domain_scores_sixty_outside_news_and_sixty_six_inside(self):
        url = "https://arxiv.org/abs/2501.00001"
        self.assertEqual(score(merged("news", self.base), url, "news")["score"], 66.0)
        self.assertEqual(score(merged("academic", self.base), url, "ai",
                               {"scholar": {"citations": 0, "year": None,
                                            "age_years": 0.0,
                                            "peer_reviewed": False}})["tier"], "3")
        self.assertEqual(
            P.tier_base(merged("academic", self.base), "3"), 60.0)


# ---------------------------------------------------------------------------
# Claim 2: apply_domain_overrides moves rather than copies, loses nothing from
# the base table, and keeps a mode-local registration inside its own mode
# ---------------------------------------------------------------------------

class OverrideInvariantTests(unittest.TestCase):
    def setUp(self):
        self.base = base_policy()
        self.base_set = set(flat_domains(self.base))

    def test_applying_any_mode_does_not_mutate_the_base_policy(self):
        """The base policy dict is handed to every mode in turn by
        check_policy; a merge that edited it in place would make mode order
        decide the result."""
        snapshot = copy.deepcopy(self.base)
        for mode in MODES:
            merged(mode, self.base)
            merged(mode, self.base)
        self.assertEqual(self.base, snapshot)

    def test_every_mode_validates_after_merge(self):
        for mode in MODES:
            with self.subTest(mode=mode):
                S.validate_policy(merged(mode, self.base), "mode:%s" % mode)

    def test_no_domain_is_filed_under_two_tiers_in_any_mode(self):
        for mode in MODES:
            with self.subTest(mode=mode):
                flat = flat_domains(merged(mode, self.base))
                dupes = sorted({d for d in flat if flat.count(d) > 1})
                self.assertEqual(dupes, [], "duplicated in %s: %s" % (mode, dupes))

    def test_no_registered_domain_is_lost_by_an_override(self):
        """A remap must not DROP an entry.

        Round A asserted set equality in both directions. Round B gave
        `domain_overrides` a second job -- registering a domain the base table
        deliberately does not carry (news outlets, doc hosts, forums,
        engineering blogs live in the one mode whose question they answer) --
        so the "invented in <mode>" half is obsolete by design and is covered
        instead by test_a_mode_local_registration_stays_local. The "lost"
        half still holds: re-filing reddit must not delete it.
        """
        for mode in MODES:
            with self.subTest(mode=mode):
                got = set(flat_domains(merged(mode, self.base)))
                self.assertEqual(sorted(self.base_set - got), [],
                                 "lost in %s" % mode)

    def test_every_merged_table_is_a_superset_of_the_base_table(self):
        """The replacement for the old exact-count invariant: a merge may add
        mode-local registrations, never remove or silently renumber the base
        table's entries. Count is therefore >= base count, and the excess is
        exactly the overlay's unregistered keys."""
        for mode in MODES:
            overlay_keys = {str(k).lower()
                            for k in (P.load_mode_overlay(mode).get("domain_overrides") or {})}
            m = merged(mode, self.base)
            flat = flat_domains(m)
            with self.subTest(mode=mode):
                self.assertGreaterEqual(len(flat), len(flat_domains(self.base)))
                self.assertEqual(sorted(set(flat) - self.base_set),
                                 sorted(overlay_keys - self.base_set))
                self.assertEqual(len(flat) - len(flat_domains(self.base)),
                                 len(overlay_keys - self.base_set))

    def test_a_mode_local_registration_stays_local(self):
        """A domain registered only in mode X must resolve to the unregistered
        default tier in a mode that does not register it -- that is what makes
        the registration mode-local rather than a back door into the base
        table. fasterthanli.me is evidence about production practice and is
        not a vetted source for a scholarly claim.

        A broader pattern that the other mode legitimately carries may still
        match (superset.apache.org falls back to the base table's apache.org,
        planetscale.com/docs to non-academic's own planetscale.com); what must
        never happen is the domain's OWN pattern being matched outside the
        mode that registered it.
        """
        default_tier = str(self.base["defaults"]["unregistered_tier"])
        owners = {}
        for mode in MODES:
            for k in (P.load_mode_overlay(mode).get("domain_overrides") or {}):
                k = str(k).lower()
                if k in self.base_set:
                    continue  # a re-file, not a registration
                owners.setdefault(k, set()).add(mode)
        self.assertTrue(owners, "round B registers mode-local domains; none found")
        for domain, registering in sorted(owners.items()):
            for mode in MODES:
                if mode in registering:
                    continue
                with self.subTest(domain=domain, mode=mode):
                    tier, matched = SC.match_tier("https://%s/some/page" % domain,
                                                  merged(mode, self.base))
                    self.assertNotEqual(
                        str(matched or "").lower(), domain,
                        "%s is visible as its own pattern in %s, which does not "
                        "register it" % (domain, mode))
                    if matched is None:
                        self.assertEqual(tier, default_tier)

    def test_every_override_lands_in_the_tier_it_names(self):
        for mode in MODES:
            overlay = P.load_mode_overlay(mode)
            overrides = overlay.get("domain_overrides") or {}
            m = merged(mode, self.base)
            idx = domain_index(m)
            for pat, tier in overrides.items():
                with self.subTest(mode=mode, domain=pat):
                    self.assertEqual(idx.get(str(pat).lower()), str(tier))

    def test_every_override_is_gone_from_its_previous_tier_list(self):
        base_idx = domain_index(self.base)
        for mode in MODES:
            overlay = P.load_mode_overlay(mode)
            m = merged(mode, self.base)
            for pat, tier in (overlay.get("domain_overrides") or {}).items():
                old = base_idx.get(str(pat).lower())
                if old is None or old == str(tier):
                    continue
                with self.subTest(mode=mode, domain=pat):
                    self.assertNotIn(str(pat).lower(),
                                     [str(d).lower() for d in m["domains"][old]])

    def test_every_override_target_tier_exists_and_is_not_a_dead_end(self):
        for mode in MODES:
            overlay = P.load_mode_overlay(mode)
            m = merged(mode, self.base)
            for pat, tier in (overlay.get("domain_overrides") or {}).items():
                with self.subTest(mode=mode, domain=pat):
                    self.assertIn(str(tier), m["tiers"])

    def test_news_overrides_both_refile_and_register(self):
        """Round A required every news override to name a base-table domain.
        Round B inverted that: the news ladder is expected to register outlets
        the academic table deliberately omits (asserting that koreaherald.com
        is an academic source would be false), so both jobs must be present --
        at least one re-file of a base domain and at least one fresh
        registration -- and every fresh registration must still validate and
        resolve (covered by the tests above).
        """
        overrides = P.load_mode_overlay("news")["domain_overrides"]
        refiled = [k for k in overrides if k.lower() in self.base_set]
        registered = [k for k in overrides if k.lower() not in self.base_set]
        self.assertTrue(refiled, "news no longer re-files any base-table domain")
        self.assertTrue(registered, "news registers no new outlet of its own")

    def test_match_tier_agrees_with_the_merged_table_for_every_override(self):
        """The table is only half the story - match_tier's longest-pattern rule
        could still route a URL somewhere else."""
        m = merged("news", self.base)
        for pat, tier in P.load_mode_overlay("news")["domain_overrides"].items():
            with self.subTest(domain=pat):
                got, _ = SC.match_tier("https://%s/some/article" % pat, m)
                self.assertEqual(got, str(tier))


# ---------------------------------------------------------------------------
# Claim 3: the news ladder is strictly ordered, and lands where it says
# ---------------------------------------------------------------------------

class NewsLadderOrderingTests(unittest.TestCase):
    def setUp(self):
        self.news = merged("news")

    def test_tier_bases_are_strictly_decreasing(self):
        bases = [P.tier_base(self.news, t) for t in ("1", "2", "3", "4", "5", "6")]
        self.assertEqual(bases, sorted(bases, reverse=True))
        for a, b in zip(bases, bases[1:]):
            self.assertGreater(a, b)

    def test_each_rung_resolves_to_its_tier(self):
        for tier, url in NEWS_LADDER:
            with self.subTest(url=url):
                self.assertEqual(score(self.news, url, "news")["tier"], tier)

    def test_scores_are_strictly_ordered_down_the_ladder(self):
        scores = [score(self.news, url, "news")["score"] for _, url in NEWS_LADDER]
        for a, b in zip(scores, scores[1:]):
            self.assertGreater(a, b)

    def test_a_bare_news_url_scores_exactly_its_tier_base(self):
        """No citation record, no HN thread, no scholar date: with peer review
        off and nothing to decay, the tier base is the whole score. That is
        what makes the tier table the load-bearing decision here."""
        for tier, url in NEWS_LADDER:
            with self.subTest(url=url):
                self.assertEqual(score(self.news, url, "news")["score"],
                                 P.tier_base(self.news, tier))

    def test_tier_three_is_inside_the_support_band_and_tier_four_is_not(self):
        support = band_min(self.news, "SUPPORT")
        self.assertGreaterEqual(P.tier_base(self.news, "3"), support)
        self.assertLess(P.tier_base(self.news, "4"), support)
        self.assertEqual(score(self.news, NEWS_LADDER[2][1], "news")["verdict"],
                         "SUPPORT")
        self.assertEqual(score(self.news, NEWS_LADDER[3][1], "news")["verdict"],
                         "SKIM")

    def test_tier_two_is_support_and_tier_one_is_primary(self):
        self.assertEqual(score(self.news, NEWS_LADDER[0][1], "news")["verdict"],
                         "PRIMARY")
        self.assertEqual(score(self.news, NEWS_LADDER[1][1], "news")["verdict"],
                         "SUPPORT")

    def test_unregistered_stays_below_the_pass_mark(self):
        r = score(self.news, NEWS_LADDER[4][1], "news")
        self.assertEqual(r["tier"], "5")
        self.assertNotIn(r["verdict"], ("SUPPORT", "PRIMARY"))

    def test_wire_services_outrank_law_firms_outrank_trade_media(self):
        wire = score(self.news, "https://apnews.com/article/x", "news")["score"]
        firm = score(self.news, "https://www.cliffordchance.com/insights/x", "news")["score"]
        trade = score(self.news, "https://www.theverge.com/2026/1/1/x", "news")["score"]
        self.assertGreater(wire, firm)
        self.assertGreater(firm, trade)

    def test_the_instrument_outranks_every_account_of_it(self):
        gazette = score(self.news, "https://eur-lex.europa.eu/legal-content/x", "news")
        self.assertEqual(gazette["tier"], "1")
        for url in ("https://www.reuters.com/x", "https://www.brookings.edu/x",
                    "https://www.theverge.com/x"):
            with self.subTest(url=url):
                self.assertGreater(gazette["score"],
                                   score(self.news, url, "news")["score"])


class NewsBandBoundaryTests(unittest.TestCase):
    """The bands are the point of the re-base, so probe the edges directly
    rather than trusting the arithmetic."""

    def setUp(self):
        self.news = merged("news")

    def test_the_support_edge_is_exact(self):
        support = band_min(self.news, "SUPPORT")
        self.assertEqual(P.verdict_for(self.news, support), "SUPPORT")
        self.assertEqual(P.verdict_for(self.news, support - 0.01), "SKIM")

    def test_tier_three_clears_the_edge_with_room_to_spare(self):
        self.assertGreater(P.tier_base(self.news, "3"),
                           band_min(self.news, "SUPPORT"))

    def test_tier_three_does_not_reach_primary(self):
        """66 must stay a supporting verdict: re-report is not the instrument."""
        self.assertLess(P.tier_base(self.news, "3"), band_min(self.news, "PRIMARY"))
        self.assertEqual(P.verdict_for(self.news, P.tier_base(self.news, "3")),
                         "SUPPORT")

    def test_tier_four_sits_exactly_on_the_skim_edge(self):
        self.assertEqual(P.tier_base(self.news, "4"), band_min(self.news, "SKIM"))
        self.assertEqual(P.verdict_for(self.news, P.tier_base(self.news, "4")),
                         "SKIM")

    def test_bands_are_unchanged_by_the_news_overlay(self):
        self.assertEqual(self.news["verdicts"], base_policy()["verdicts"])


class TierThreeRebaseRegressionGuard(unittest.TestCase):
    """The guard PILOT F11 asks for: if the 60 -> 66 re-base is reverted, the
    whole re-report rung falls out of the band it was registered to reach."""

    def setUp(self):
        self.news = merged("news")

    def test_the_rebase_is_present(self):
        self.assertEqual(P.tier_base(self.news, "3"), 66.0)
        self.assertNotEqual(P.tier_base(self.news, "3"),
                            P.tier_base(base_policy(), "3"))

    def test_reverting_the_rebase_demotes_the_whole_rung(self):
        reverted = copy.deepcopy(self.news)
        reverted["tiers"]["3"]["base"] = 60
        S.validate_policy(reverted, "reverted")
        for url in ("https://www.gibsondunn.com/x", "https://www.brookings.edu/x",
                    "https://www.lawfaremedia.org/article/x",
                    "https://iapp.org/news/a/x"):
            with self.subTest(url=url):
                self.assertEqual(score(self.news, url, "news")["verdict"], "SUPPORT")
                self.assertNotEqual(score(reverted, url, "news")["verdict"], "SUPPORT")

    def test_the_old_pass_mark_was_genuinely_unreachable(self):
        """60 < 62 with every adjusting signal off is the F10/F11 bug itself:
        registering domains at tier 3 would have moved nothing."""
        self.assertLess(60, band_min(self.news, "SUPPORT"))

    def test_registering_a_domain_at_tier_three_now_actually_promotes_it(self):
        academic = merged("academic")
        for url in ("https://www.gibsondunn.com/x",
                    "https://www.lawfaremedia.org/article/x",
                    "https://www.scotusblog.com/2026/01/x/"):
            with self.subTest(url=url):
                self.assertNotIn(score(academic, url, "ai")["verdict"],
                                 ("SUPPORT", "PRIMARY"))
                self.assertEqual(score(self.news, url, "news")["verdict"], "SUPPORT")

    def test_the_ladder_demotes_think_tanks_as_well_as_promoting_law_firms(self):
        """The tier-3 rung is not only a promotion: the base table files policy
        institutes at tier 2 (74) and news moves them down to 66, so a
        Brookings paper ranks below a Reuters report in this mode. Same
        verdict band, deliberately different score."""
        academic = merged("academic")
        for url in ("https://www.brookings.edu/x", "https://www.rand.org/x",
                    "https://iapp.org/news/a/x"):
            with self.subTest(url=url):
                self.assertGreater(score(academic, url, "ai")["score"],
                                   score(self.news, url, "news")["score"])
        self.assertGreater(score(self.news, "https://www.reuters.com/x", "news")["score"],
                           score(self.news, "https://www.brookings.edu/x", "news")["score"])


# ---------------------------------------------------------------------------
# Claim 4: syndication aggregators are deliberately held at tier 4
# ---------------------------------------------------------------------------

class SyndicationAggregatorTests(unittest.TestCase):
    def setUp(self):
        self.news = merged("news")

    def test_aggregators_are_not_on_the_tier_three_ladder(self):
        overrides = P.load_mode_overlay("news")["domain_overrides"]
        for d in ("jdsupra.com", "lexology.com"):
            with self.subTest(domain=d):
                self.assertNotIn(d, overrides)

    def test_aggregators_stay_at_trade_media_tier_four(self):
        for url in ("https://www.jdsupra.com/legalnews/some-alert-1234/",
                    "https://www.lexology.com/library/detail.aspx?g=abc"):
            with self.subTest(url=url):
                r = score(self.news, url, "news")
                self.assertEqual(r["tier"], "4")
                self.assertEqual(r["verdict"], "SKIM")

    def test_an_aggregator_scores_below_the_firm_whose_alert_it_redistributes(self):
        agg = score(self.news, "https://www.jdsupra.com/legalnews/x/", "news")["score"]
        firm = score(self.news, "https://www.dlapiper.com/en/insights/x", "news")["score"]
        self.assertGreater(firm, agg)

    def test_natlawreview_is_treated_as_an_aggregator_like_its_peers(self):
        """natlawreview.com republishes firm-authored alerts exactly as
        jdsupra and lexology do, so it takes the same call: off the tier-3
        analysis ladder, held at trade-media tier 4. This suite pinned it at
        tier 3 when written; that inconsistency was the finding, and this is
        the resolved side of it."""
        self.assertEqual(score(self.news, "https://natlawreview.com/article/x",
                               "news")["tier"], "4")


# ---------------------------------------------------------------------------
# Claim 5: the other four modes are untouched
# ---------------------------------------------------------------------------

class OtherModesUnchangedTests(unittest.TestCase):
    """The parts of claim 5 that do hold: no mode file other than news carries
    a `tiers` block, and no other mode's switches, bands or defaults moved."""

    def setUp(self):
        self.base = base_policy()

    def test_news_is_the_only_overlay_with_a_tiers_block(self):
        for mode in OTHER_MODES:
            with self.subTest(mode=mode):
                self.assertNotIn("tiers", P.load_mode_overlay(mode))
        self.assertIn("tiers", P.load_mode_overlay("news"))

    def test_academic_mode_is_still_a_true_no_op(self):
        self.assertEqual(merged("academic", self.base), self.base)

    def test_other_modes_keep_their_signal_switches(self):
        expect = {
            "academic": (True, True, True),
            "non-academic": (True, True, True),
            "community-opinion": (True, False, True),
            "official-docs": (False, False, False),
        }
        for mode, (rec, peer, eng) in expect.items():
            m = merged(mode, self.base)
            with self.subTest(mode=mode):
                self.assertEqual(S.signal_enabled(m, "recency_decay"), rec)
                self.assertEqual(S.signal_enabled(m, "peer_review"), peer)
                self.assertEqual(S.signal_enabled(m, "engagement"), eng)

    def test_other_modes_keep_their_default_field(self):
        # official-docs sets no field of its own and inherits the base "ai";
        # harmless there only because recency is switched off in that mode.
        for mode, field in (("academic", "ai"), ("non-academic", "cs"),
                            ("community-opinion", "opinion"),
                            ("official-docs", "ai")):
            with self.subTest(mode=mode):
                self.assertEqual(merged(mode, self.base)["defaults"]["field"], field)

    def test_the_landmark_cases_of_the_other_modes_still_hold(self):
        co = merged("community-opinion", self.base)
        self.assertIn(score(co, "https://news.ycombinator.com/item?id=42",
                            "opinion", {"hn": {"points": 400}})["verdict"],
                      ("SUPPORT", "PRIMARY"))
        od = merged("official-docs", self.base)
        self.assertIn(score(od, "https://docs.python.org/3/library/asyncio.html",
                            "cs")["verdict"], ("SUPPORT", "PRIMARY"))
        self.assertEqual(score(od, "https://netflixtechblog.com/p", "cs")["tier"], "3")

    def test_the_news_ladder_does_not_leak_into_the_other_modes(self):
        for mode, field in (("academic", "ai"), ("non-academic", "cs"),
                            ("community-opinion", "opinion"),
                            ("official-docs", "cs")):
            m = merged(mode, self.base)
            for url in ("https://www.reuters.com/x", "https://www.bbc.com/news/x",
                        "https://www.gibsondunn.com/x"):
                with self.subTest(mode=mode, url=url):
                    self.assertNotEqual(score(m, url, field)["tier"], "2")


class BaseExpansionSideEffectTests(unittest.TestCase):
    """The part of claim 5 that does NOT hold. The ~94 domains this change adds
    to scripts/policy.json are shared by every mode, so four modes that were
    never meant to change did change: these domains used to fall to the
    unregistered tier 5 (32, WEAK) in every mode and no longer do.

    Characterisation, not endorsement - each assertion pins a score movement
    that the change's own description says did not happen.
    """

    def setUp(self):
        self.base = base_policy()

    def test_newly_registered_domains_moved_in_the_academic_profile(self):
        academic = merged("academic", self.base)
        for url, tier in (("https://www.eff.org/deeplinks/x", "3"),
                          ("https://noyb.eu/en/x", "3"),
                          ("https://www.gibsondunn.com/x", "4"),
                          ("https://cyberscoop.com/x", "4"),
                          ("https://www.axios.com/x", "4"),
                          ("https://iapp.org/news/x", "2"),
                          ("https://turing.ac.uk/x", "2")):
            with self.subTest(url=url):
                r = score(academic, url, "ai")
                self.assertEqual(r["tier"], tier)
                self.assertNotEqual(r["tier"],
                                    str(self.base["defaults"]["unregistered_tier"]))

    def test_the_movement_is_identical_in_all_four_non_news_modes(self):
        """It is a base-table change, so it lands everywhere at once - which is
        why "only news changed" cannot be true of it."""
        for mode, field in (("academic", "ai"), ("non-academic", "cs"),
                            ("community-opinion", "opinion"),
                            ("official-docs", "cs")):
            with self.subTest(mode=mode):
                self.assertEqual(
                    score(merged(mode, self.base), "https://iapp.org/news/x",
                          field)["tier"], "2")


class KnownGapTests(unittest.TestCase):
    """Properties the design implies but the policy does not currently hold.
    Each is marked expectedFailure with its evidence; a green result here means
    the gap was closed and the decorator should be dropped."""

    def setUp(self):
        self.base = base_policy()

    def test_a_papers_of_record_outlet_is_recognised_on_every_host_it_uses(self):
        """CLOSED GAP, kept as a regression guard. bbc.co.uk was unregistered
        while bbc.com sat at tier 2 - the same outlet, a 42-point spread
        decided by which host the search result happened to return. Any outlet
        promoted onto the tier-2 rung has to be listed under every host it
        publishes from, or the ladder is deciding on a URL detail rather than
        on the publisher."""
        news = merged("news", self.base)
        for a, b in (("https://www.bbc.co.uk/news/x", "https://www.bbc.com/news/x"),
                     ("https://guardian.co.uk/x", "https://www.theguardian.com/x")):
            with self.subTest(hosts=(a, b)):
                self.assertEqual(score(news, a, "news")["tier"],
                                 score(news, b, "news")["tier"])

    def test_official_docs_does_not_treat_trade_bodies_as_documentation(self):
        """PARTIALLY CLOSED GAP, decorator dropped. official-docs turns every
        signal off, so score == tier base and EVERY base tier-2 domain used to
        pass it: mit.edu, owasp.org and mayoclinic.org all scored 74 SUPPORT
        there, and the base-table expansion widened the set by six (iapp,
        turing, fpf, ada lovelace, ai now, cloud security alliance) rather than
        creating the behaviour.

        What closed it for these two URLs is the commentary path read that mode
        now carries (penalties.seo_path re-pointed at /news/, /blog/ and the
        rest at -28): a trade body's news item is no longer read as reference.
        Per this suite's own convention a passing expectedFailure has its
        decorator dropped, so it is now an ordinary regression guard.

        The residual is real and still unfixed: the close is a path read, not a
        ceiling, so a tier-2 domain whose URL carries no commentary segment
        (iapp.org/resources/article/..., mit.edu/..., owasp.org/Top10/) still
        scores 74 SUPPORT in a documentation search. That is pinned by
        test_a_tier_two_domain_with_no_commentary_segment_is_still_support
        below, and the real fix remains a ceiling in official-docs for domains
        it has not re-filed as documentation."""
        od = merged("official-docs", self.base)
        for url in ("https://iapp.org/news/a/some-privacy-story",
                    "https://www.turing.ac.uk/blog/x"):
            with self.subTest(url=url):
                self.assertNotIn(score(od, url, "cs")["verdict"],
                                 ("SUPPORT", "PRIMARY"))

    @unittest.expectedFailure
    def test_a_tier_two_domain_with_no_commentary_segment_is_still_support(self):
        """OPEN GAP, the residual of the one above. official-docs re-files the
        hosts it considers documentation and reads commentary segments out of
        the citable band, but it sets no ceiling for a base tier-2 domain it
        never re-filed: a university, a standards-adjacent association or a
        hospital still scores 74 SUPPORT in a documentation search as long as
        the URL carries no /news/ or /blog/ segment. Still a change to that
        mode rather than to the news ladder."""
        od = merged("official-docs", self.base)
        for url in ("https://iapp.org/resources/article/some-guidance/",
                    "https://www.mit.edu/some/page",
                    "https://owasp.org/Top10/",
                    "https://www.mayoclinic.org/diseases-conditions/x"):
            with self.subTest(url=url):
                self.assertNotIn(score(od, url, "cs")["verdict"],
                                 ("SUPPORT", "PRIMARY"))

    def test_market_research_does_not_outrank_wire_reporting_by_band(self):
        """CLOSED GAP, kept as a regression guard. The tier-3 re-base lifts the
        whole base tier-3 list, which the mode intends for vendor engineering
        blogs and preprints - but the list also holds gartner, forrester, idc
        and mdpi, which rode it to 66 SUPPORT, the same verdict band as a
        Reuters report. They are now pinned back to tier 4 by the overlay. A
        firm selling the analysis, or a pay-to-publish venue, must not share a
        band with a wire service."""
        news = merged("news", self.base)
        for url in ("https://www.gartner.com/en/newsroom/x",
                    "https://www.forrester.com/blogs/x",
                    "https://www.mdpi.com/1/2/3"):
            with self.subTest(url=url):
                self.assertNotIn(score(news, url, "news")["verdict"],
                                 ("SUPPORT", "PRIMARY"))


# ---------------------------------------------------------------------------
# End to end through the CLI, so the overlay is exercised the way the skill
# actually invokes it.
# ---------------------------------------------------------------------------

class NewsCliTests(unittest.TestCase):
    def test_news_mode_scores_the_ladder_end_to_end(self):
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            rc = CLI.main(["--mode", "news", "--no-net", "--format", "json"]
                          + [a for _, u in NEWS_LADDER for a in ("-u", u)])
        self.assertEqual(rc, 0, msg=err.getvalue())
        rows = json.loads(out.getvalue())
        by_url = {r["url"]: r for r in rows}
        self.assertEqual(len(rows), len(NEWS_LADDER))
        for tier, url in NEWS_LADDER:
            with self.subTest(url=url):
                self.assertEqual(by_url[url]["tier"], tier)
        scores = [by_url[u]["score"] for _, u in NEWS_LADDER]
        for a, b in zip(scores, scores[1:]):
            self.assertGreater(a, b)


if __name__ == "__main__":
    unittest.main()
