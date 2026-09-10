import unittest

import _pathsetup  # noqa: F401
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


def merged(mode, policy=None):
    return P.apply_mode(policy or base_policy(), mode)


def tier_of(policy, url):
    """Tier as the scorer resolves it -- match_tier, not a dict lookup, so the
    longest-pattern rule is part of what is being asserted."""
    return SC.match_tier(url, policy)[0]


def score(policy, url, field=None, injected=None):
    return SC.score_one({"url": url}, policy, NullCache(),
                        field or policy["defaults"]["field"], False,
                        injected or {})


def band_index(policy, score_value):
    """Position of `score_value` in the verdict ladder: 0 = PRIMARY, higher =
    worse. Lets "drops a band" be asserted without hardcoding band names."""
    for i, b in enumerate(policy["verdicts"]["bands"]):
        if score_value >= float(b["min"]):
            return i
    return len(policy["verdicts"]["bands"]) - 1


def overrides(mode):
    return P.load_mode_overlay(mode).get("domain_overrides") or {}


# ---------------------------------------------------------------------------
# Claim 1: the non-academic ladder
# ---------------------------------------------------------------------------

CODE_HOSTS = ["github.com", "gitlab.com", "stackoverflow.com", "stackexchange.com",
              "codeberg.org", "git.sr.ht", "bitbucket.org"]
OPEN_SUBMISSION = ["hackernoon.com", "c-sharpcorner.com", "codeproject.com"]


class NonAcademicLadderTests(unittest.TestCase):
    """"the artifact is the evidence": code hosts rise to 3, engineering blogs
    sit at 4, an abandoned code host falls to 5, open-submission dev-content
    platforms to 6."""

    def setUp(self):
        self.base = base_policy()
        self.na = merged("non-academic", self.base)

    def test_code_hosts_are_tier_three(self):
        for d in CODE_HOSTS:
            with self.subTest(domain=d):
                self.assertEqual(tier_of(self.na, "https://%s/owner/repo" % d), "3")

    def test_code_hosts_are_co_tiered_rather_than_ranked(self):
        """No code host is privileged over another: engagement, not the table,
        is what separates two repos in this mode."""
        tiers = {d: tier_of(self.na, "https://%s/owner/repo" % d) for d in CODE_HOSTS}
        self.assertEqual(len(set(tiers.values())), 1, tiers)

    def test_code_hosts_rise_above_where_the_academic_table_files_them(self):
        """github 4 -> 3 and stackoverflow 5 -> 3: the point of the ladder."""
        academic = merged("academic", self.base)
        for d, was in (("github.com", "4"), ("stackoverflow.com", "5"),
                       ("stackexchange.com", "5")):
            with self.subTest(domain=d):
                self.assertEqual(tier_of(academic, "https://%s/x" % d), was)
                self.assertEqual(tier_of(self.na, "https://%s/x" % d), "3")

    def test_engineering_blogs_are_tier_four(self):
        """Company and individual alike -- the band the base table already
        files danluu/jvns.ca/martinfowler at."""
        for d in ("dropbox.tech", "airbnb.tech", "engineering.atspotify.com",
                  "engineering.linkedin.com", "honeycomb.io", "tailscale.com",
                  "toss.tech", "d2.naver.com", "techblog.woowahan.com",
                  "fasterthanli.me", "hillelwayne.com", "aphyr.com",
                  "lemire.me", "apenwarr.ca"):
            with self.subTest(domain=d):
                self.assertEqual(tier_of(self.na, "https://%s/some-post" % d), "4")

    def test_every_blog_the_overlay_files_at_four_resolves_to_four(self):
        """Not a spot check: every entry the overlay names as tier 4 must come
        back as tier 4 through match_tier, so no broader pattern shadows one."""
        for d, tier in overrides("non-academic").items():
            if str(tier) != "4":
                continue
            with self.subTest(domain=d):
                self.assertEqual(tier_of(self.na, "https://%s/some-post" % d), "4")

    def test_engineering_blogs_rank_below_code_hosts(self):
        self.assertGreater(P.tier_base(self.na, "3"), P.tier_base(self.na, "4"))

    def test_sourceforge_is_tier_five(self):
        """A code host whose listings are mostly abandoned is not the live
        artifact this mode reads."""
        self.assertEqual(tier_of(self.na, "https://sourceforge.net/projects/x/"), "5")

    def test_sourceforge_ranks_below_every_other_code_host(self):
        sf = P.tier_base(self.na, tier_of(self.na, "https://sourceforge.net/p/x"))
        for d in CODE_HOSTS:
            with self.subTest(domain=d):
                self.assertGreater(
                    P.tier_base(self.na, tier_of(self.na, "https://%s/o/r" % d)), sf)

    def test_open_submission_platforms_are_tier_six(self):
        for d in OPEN_SUBMISSION:
            with self.subTest(domain=d):
                self.assertEqual(tier_of(self.na, "https://%s/some-post" % d), "6")

    def test_open_submission_platforms_do_not_reach_a_citable_verdict(self):
        for d in OPEN_SUBMISSION:
            with self.subTest(domain=d):
                self.assertNotIn(score(self.na, "https://%s/p" % d, "cs")["verdict"],
                                 ("PRIMARY", "SUPPORT", "SKIM"))

    def test_the_four_rungs_are_strictly_ordered(self):
        rungs = ["https://github.com/owner/repo",
                 "https://fasterthanli.me/articles/x",
                 "https://sourceforge.net/projects/x/",
                 "https://hackernoon.com/x"]
        bases = [P.tier_base(self.na, tier_of(self.na, u)) for u in rungs]
        for a, b in zip(bases, bases[1:]):
            self.assertGreater(a, b)


class NonAcademicArchivedRepoTests(unittest.TestCase):
    """archived_penalty is -20 in this mode and -4 in the base table: once code
    hosts sit at tier 3, the base penalty left an abandoned repo in the same
    verdict band as a live one, which contradicts the mode's own claim that the
    LIVE artifact is the evidence."""

    def setUp(self):
        self.base = base_policy()
        self.na = merged("non-academic", self.base)

    def test_the_penalty_is_minus_twenty_here_and_minus_four_in_the_base(self):
        self.assertEqual(self.na["engagement"]["github"]["archived_penalty"], -20.0)
        self.assertEqual(self.base["engagement"]["github"]["archived_penalty"], -4.0)

    def test_no_other_mode_deepens_the_archived_penalty(self):
        for mode in MODES:
            if mode == "non-academic":
                continue
            with self.subTest(mode=mode):
                self.assertEqual(
                    merged(mode, self.base)["engagement"]["github"]["archived_penalty"],
                    -4.0)

    def test_an_archived_repo_lands_a_band_below_an_equivalent_live_one(self):
        url = "https://github.com/owner/repo"
        for stars in (100, 1200, 5000, 50000):
            live = score(self.na, url, "cs",
                         {"github": {"stars": stars, "archived": False}})
            dead = score(self.na, url, "cs",
                         {"github": {"stars": stars, "archived": True}})
            with self.subTest(stars=stars):
                self.assertAlmostEqual(dead["score"], live["score"] - 20.0, places=6)
                self.assertGreater(band_index(self.na, dead["score"]),
                                   band_index(self.na, live["score"]),
                                   "archived %d-star repo kept %s's band"
                                   % (stars, live["verdict"]))

    def test_the_base_penalty_would_not_have_cost_a_band_at_1200_stars(self):
        """Why the mode needed its own number: with -4 the abandoned repo stays
        in the live repo's band, which is the behaviour this change rejects."""
        weak = dict(self.na)
        weak["engagement"] = {
            "github": dict(self.na["engagement"]["github"], archived_penalty=-4.0),
            "hackernews": self.na["engagement"]["hackernews"],
        }
        url = "https://github.com/owner/repo"
        live = score(weak, url, "cs", {"github": {"stars": 1200, "archived": False}})
        dead = score(weak, url, "cs", {"github": {"stars": 1200, "archived": True}})
        self.assertEqual(band_index(weak, dead["score"]),
                         band_index(weak, live["score"]))
        self.assertEqual(dead["verdict"], "SUPPORT")

    def test_the_archived_flag_is_reported_not_silent(self):
        r = score(self.na, "https://github.com/owner/repo", "cs",
                  {"github": {"stars": 1200, "archived": True}})
        self.assertTrue(any("archived" in str(n) for n in r.get("signals", [])),
                        "archived repo should say so: %r" % (r.get("signals"),))


# ---------------------------------------------------------------------------
# Claim 2: official-docs -- a commentary path loses the reference claim
# ---------------------------------------------------------------------------

# (host, commentary URL, reference URL). Every host here is filed at tier 1 by
# this mode; the pair asserts that the SAME host answers differently depending
# on whether the path is the project's reference or its commentary.
DOC_HOST_SPLITS = [
    ("cloud.google.com",
     "https://cloud.google.com/blog/products/ai-machine-learning/announcing-x",
     "https://cloud.google.com/vertex-ai/docs/start/introduction"),
    ("azure.microsoft.com",
     "https://azure.microsoft.com/blog/announcing-x/",
     "https://azure.microsoft.com/en-us/products/machine-learning"),
    ("supabase.com",
     "https://supabase.com/blog/launch-week-x",
     "https://supabase.com/docs/guides/auth"),
    ("redis.io",
     "https://redis.io/blog/whats-new-in-x/",
     "https://redis.io/docs/latest/commands/get/"),
    ("elastic.co",
     "https://www.elastic.co/blog/announcing-x",
     "https://www.elastic.co/guide/en/elasticsearch/reference/current/docs-get.html"),
    ("hashicorp.com",
     "https://www.hashicorp.com/blog/announcing-x",
     "https://www.hashicorp.com/products/terraform"),
    # The hosts the per-host /blog allowlist used to miss entirely.
    ("kubernetes.io",
     "https://kubernetes.io/blog/2024/01/01/some-release/",
     "https://kubernetes.io/docs/concepts/workloads/pods/"),
    ("nextjs.org",
     "https://nextjs.org/blog/next-15",
     "https://nextjs.org/docs/app/building-your-application/routing"),
    ("react.dev",
     "https://react.dev/blog/2024/04/25/react-19",
     "https://react.dev/reference/react/useState"),
    ("pytorch.org",
     "https://pytorch.org/blog/some-announcement/",
     "https://pytorch.org/docs/stable/generated/torch.nn.Linear.html"),
    ("go.dev",
     "https://go.dev/blog/go1.22",
     "https://go.dev/ref/spec"),
    ("rust-lang.org",
     "https://www.rust-lang.org/blog/some-post",
     "https://doc.rust-lang.org/book/ch01-01-installation.html"),
    ("prisma.io",
     "https://www.prisma.io/blog/some-post",
     "https://www.prisma.io/docs/orm/prisma-schema"),
    ("temporal.io",
     "https://temporal.io/blog/some-post",
     "https://docs.temporal.io/workflows"),
    # /about/news/ rather than /blog/, and a /docs/current/ reference: the
    # version_path rule fires on a bare major line (/docs/16/), so the
    # reference URL here is the unversioned current one on purpose.
    ("postgresql.org",
     "https://www.postgresql.org/about/news/some-release-2345/",
     "https://www.postgresql.org/docs/current/sql-select.html"),
    # Sales collateral, not a blog post -- the other half of the gap.
    ("elastic.co (customers)",
     "https://www.elastic.co/customers/some-company",
     "https://www.elastic.co/guide/en/elasticsearch/reference/current/docs-get.html"),
    ("supabase.com (customers)",
     "https://supabase.com/customers/some-company",
     "https://supabase.com/docs/guides/auth"),
]

# Hosts whose reference lives under a /docs path: the host is filed at tier 3
# and only the /docs prefix is tier 1, so commentary lands a further band down.
DOCS_PREFIX_HOSTS = [
    ("mongodb.com", "https://www.mongodb.com/blog/post/some-announcement",
     "https://www.mongodb.com/docs/manual/introduction/"),
    ("vercel.com", "https://vercel.com/blog/some-post",
     "https://vercel.com/docs/functions"),
    ("grafana.com", "https://grafana.com/blog/some-post/",
     "https://grafana.com/docs/grafana/latest/introduction/"),
    ("neon.tech", "https://neon.tech/blog/some-post",
     "https://neon.tech/docs/introduction"),
    ("clickhouse.com", "https://clickhouse.com/blog/some-post",
     "https://clickhouse.com/docs/en/intro"),
]


class OfficialDocsPathOverHostTests(unittest.TestCase):
    """Rule (b): when marketing or commentary lives under a host filed at
    tier 1, the path loses the reference claim.

    The mechanism changed after this suite first reported the gap. It used to be
    a per-host allowlist of `<host>/blog` entries re-filed at tier 3; it is now
    one deterministic path read -- this mode re-points `penalties.seo_path` at
    commentary and sales segments at -28, the same one-band-out-of-citable drop
    `version_path` uses. These tests therefore assert the PROPERTY (a commentary
    path under a tier-1 host must not reach the citable band while the reference
    path on the same host does) and not the tier number, which is exactly what
    changed: a launch post now keeps tier 1 and loses 28 points.
    """

    def setUp(self):
        self.base = base_policy()
        self.od = merged("official-docs", self.base)

    def test_the_host_itself_is_tier_one(self):
        for host, _, ref in DOC_HOST_SPLITS:
            with self.subTest(host=host):
                self.assertEqual(tier_of(self.od, ref), "1")

    def test_the_reference_path_is_citable(self):
        for host, _, ref in DOC_HOST_SPLITS:
            with self.subTest(host=host):
                r = score(self.od, ref, "cs")
                self.assertEqual(r["verdict"], "PRIMARY")
                self.assertNotIn("off-reference-path", r["flags"])

    def test_a_commentary_or_sales_path_does_not_reach_the_citable_band(self):
        """The property the old per-host allowlist failed to deliver: a launch
        post or a customer story under a tier-1 host is out of the citable
        range, whatever tier the host carries."""
        for host, off, _ in DOC_HOST_SPLITS:
            with self.subTest(host=host):
                r = score(self.od, off, "cs")
                self.assertNotIn(r["verdict"], ("PRIMARY", "SUPPORT"),
                                 "%s scored %s" % (off, r["verdict"]))

    def test_the_commentary_read_is_flagged_and_costs_one_band(self):
        """Deterministic and reported, not a silent adjustment: the flag names
        the reason, and the drop is one band (out of citable, still readable)
        rather than a block."""
        for host, off, ref in DOC_HOST_SPLITS:
            with self.subTest(host=host):
                hit, clean = score(self.od, off, "cs"), score(self.od, ref, "cs")
                self.assertIn("off-reference-path", hit["flags"])
                self.assertAlmostEqual(
                    hit["score"],
                    P.tier_base(self.od, hit["tier"])
                    + self.od["penalties"]["seo_path"]["points"], places=6)
                self.assertGreater(band_index(self.od, hit["score"]),
                                   band_index(self.od, clean["score"]))

    def test_the_same_host_answers_differently_by_path(self):
        """Two URLs, one host: the path is the whole decision."""
        for host, off, ref in DOC_HOST_SPLITS:
            with self.subTest(host=host):
                self.assertGreater(score(self.od, ref, "cs")["score"],
                                   score(self.od, off, "cs")["score"])

    def test_the_penalty_is_this_modes_own_and_not_the_base_seo_read(self):
        """The base policy's seo_path is -6 at listicle slugs; this mode
        re-points the same machinery at reference-vs-commentary at -28. Both
        must stay in their own mode, or an academic search would start
        penalising every /news/ URL."""
        self.assertEqual(self.od["penalties"]["seo_path"]["points"], -28.0)
        self.assertEqual(self.od["penalties"]["seo_path"]["flag"],
                         "off-reference-path")
        self.assertEqual(self.base["penalties"]["seo_path"]["points"], -6.0)
        for mode in MODES:
            if mode == "official-docs":
                continue
            m = merged(mode, self.base)
            with self.subTest(mode=mode):
                self.assertEqual(m["penalties"]["seo_path"]["points"], -6.0)
                self.assertNotIn("/blog/", m["seo_path_patterns"])

    def test_the_drop_matches_the_version_path_convention(self):
        """Stated reason for -28: the same one-band-out-of-citable drop the
        stale-docs read already uses."""
        self.assertEqual(self.od["penalties"]["seo_path"]["points"],
                         self.od["penalties"]["version_path"]["points"])

    def test_no_per_host_blog_entry_survives_to_stack_with_the_path_read(self):
        """The twelve `<host>/blog` tier-3 entries are gone on purpose: kept
        alongside the path read they stacked (tier 3 - 28 = 32), a harsher
        verdict than either rule claims on its own."""
        for d in overrides("official-docs"):
            with self.subTest(pattern=d):
                self.assertNotIn("/blog", str(d).lower())

    def test_a_docs_prefix_host_keeps_its_reference_and_demotes_the_rest(self):
        """A host whose reference lives under /docs is filed at 3 host-wide, so
        its blog posts land a band below a tier-1 host's (32/WEAK, not
        60/SKIM) while the /docs prefix is still tier 1."""
        for host, off, ref in DOCS_PREFIX_HOSTS:
            with self.subTest(host=host):
                self.assertEqual(tier_of(self.od, ref), "1")
                self.assertEqual(score(self.od, ref, "cs")["verdict"], "PRIMARY")
                hit = score(self.od, off, "cs")
                self.assertEqual(hit["tier"], "3")
                self.assertIn("off-reference-path", hit["flags"])
                self.assertNotIn(hit["verdict"], ("PRIMARY", "SUPPORT", "SKIM"))

    def test_a_docs_prefix_host_is_registered_rather_than_left_unknown(self):
        """Leaving the host unregistered would have put its blog at the
        unregistered base minus the path penalty (32 - 28 = 4), which claims
        far more than this mode knows."""
        for host, off, _ in DOCS_PREFIX_HOSTS:
            with self.subTest(host=host):
                self.assertIsNotNone(SC.match_tier(off, self.od)[1])
                self.assertGreater(score(self.od, off, "cs")["score"], 4.0)

    def test_every_non_tier_one_override_resolves_where_the_overlay_files_it(self):
        for d, tier in overrides("official-docs").items():
            if str(tier) == "1":
                continue
            with self.subTest(pattern=d):
                self.assertEqual(tier_of(self.od, "https://%s/some-page" % d),
                                 str(tier))

    def test_the_commentary_read_does_not_fire_on_genuine_reference_pages(self):
        """OPEN GAP -- left failing deliberately, see the report.

        The generic path read replaced a per-host allowlist that could never
        stay complete, which is the right trade, but two of its segments name
        things that are also ordinary API-reference subjects rather than only
        marketing sections, and the read is a plain substring test over the
        whole path:

          /events/    an events API or an event-reference page
          /whats-new/ a vendor's release notes, which ARE documentation

        Each URL below is canonical reference material that now loses 28 points
        and lands 60/SKIM, out of the citable band -- a false negative of the
        same size as the false positive the rule was added to stop. Note the
        inconsistency this produces within one product: Stripe's /api/events is
        demoted while /api/charges is PRIMARY, and Node's /docs/latest/api/
        events/ is demoted while /api/events.html is PRIMARY, so the verdict
        turns on whether the reference page happens to end in a slash.

        Candidate fixes, both still deterministic: anchor the commentary
        segments at the start of the path (marketing lives at /blog/, /events/
        directly under the host, not nine segments deep under /docs/), or
        exempt a path that also carries a reference segment (/docs/, /api/,
        /reference/, /guide/). "/events/" was worth keeping either way --
        a vendor conference page is not reference -- it just needs to stop
        matching mid-path.
        """
        for url in ("https://docs.stripe.com/api/events",
                    "https://docs.stripe.com/api/events/types",
                    "https://developer.mozilla.org/en-US/docs/Web/Events/",
                    "https://nodejs.org/docs/latest/api/events/",
                    "https://docs.github.com/en/rest/activity/events",
                    "https://docs.docker.com/reference/cli/docker/system/events/",
                    "https://docs.datadoghq.com/api/latest/events/",
                    "https://learn.microsoft.com/en-us/azure/event-grid/events/",
                    "https://learn.microsoft.com/en-us/dotnet/core/whats-new/dotnet-9/overview",
                    "https://learn.microsoft.com/en-us/windows/whats-new/whats-new-windows-11-version-24h2"):
            with self.subTest(url=url):
                r = score(self.od, url, "cs")
                self.assertNotIn("off-reference-path", r["flags"])
                self.assertEqual(r["verdict"], "PRIMARY")

    def test_huggingface_host_is_not_tier_one_but_its_docs_path_is(self):
        """Only huggingface.co/docs is the project's reference; the host also
        carries model pages, posts and a blog, so a host-wide tier 1 would make
        a model card score as documentation."""
        self.assertNotEqual(tier_of(self.od, "https://huggingface.co/meta-llama/x"),
                            "1")
        self.assertEqual(tier_of(self.od, "https://huggingface.co/docs/transformers/x"),
                         "1")
        self.assertNotIn(score(self.od, "https://huggingface.co/blog/x", "cs")["verdict"],
                         ("PRIMARY", "SUPPORT"))

    def test_huggingface_host_is_not_registered_at_tier_one_in_the_overlay(self):
        self.assertNotEqual(str(overrides("official-docs").get("huggingface.co")), "1")
        self.assertEqual(str(overrides("official-docs").get("huggingface.co/docs")), "1")

    def test_lore_kernel_org_does_not_inherit_kernel_orgs_tier_one(self):
        """kernel.org is the reference; lore.kernel.org is a mailing-list
        archive -- discussion about the kernel, not the kernel's own docs. The
        subdomain must be listed separately or host matching hands it tier 1."""
        self.assertEqual(tier_of(self.od, "https://www.kernel.org/doc/html/latest/x"),
                         "1")
        self.assertEqual(tier_of(self.od, "https://lore.kernel.org/lkml/abc123/"), "3")
        self.assertLess(
            score(self.od, "https://lore.kernel.org/lkml/abc123/", "cs")["score"],
            score(self.od, "https://www.kernel.org/doc/html/latest/x", "cs")["score"])

    def test_a_doc_shaped_host_that_is_not_the_projects_own_output_is_not_tier_one(self):
        """Rule (c) of the mode's _readme: third-party man-page mirrors and
        community guides stay where the base table has them."""
        for url in ("https://linux.die.net/man/1/ls", "https://zig.guide/chapter-1/"):
            with self.subTest(url=url):
                self.assertNotEqual(tier_of(self.od, url), "1")


# ---------------------------------------------------------------------------
# Claim 3: the news tier-2 band is newsroom shape, not editorial lean
# ---------------------------------------------------------------------------

# Pairs the mode's _ladder_rules names as deliberately co-tiered across a
# political split. If either side of a pair moves, the ladder has started
# encoding a lean.
CO_TIERED_PAIRS = [("theguardian.com", "telegraph.co.uk"),
                   ("hani.co.kr", "chosun.com"),
                   ("zeit.de", "welt.de")]
CABLE_NETWORKS = ["cnn.com", "foxnews.com", "msnbc.com"]
PRESS_RELEASE_WIRES = ["prnewswire.com", "businesswire.com", "globenewswire.com",
                       "accesswire.com", "einpresswire.com", "openpr.com",
                       "prlog.org", "newswire.com"]
REPUBLISHING_WRAPPERS = ["msn.com", "flipboard.com", "news.google.com"]


def ladder_rules_text():
    return " ".join(P.load_mode_overlay("news").get("_ladder_rules") or []).lower()


class NewsNewsroomShapeTests(unittest.TestCase):
    def setUp(self):
        self.base = base_policy()
        self.news = merged("news", self.base)

    def test_the_ladder_rules_name_the_pairs_it_claims_to_co_tier(self):
        """Guards the premise of this class: the assertions below are only
        meaningful if the mode really does claim these are co-tiered."""
        text = ladder_rules_text()
        if not text:
            self.skipTest("_ladder_rules is optional commentary")
        for word in ("guardian", "telegraph", "hani", "chosun", "zeit", "welt"):
            with self.subTest(word=word):
                self.assertIn(word, text)
        self.assertIn("editorial lean", text)

    def test_outlets_across_a_political_split_sit_at_the_same_tier(self):
        for left, right in CO_TIERED_PAIRS:
            with self.subTest(pair=(left, right)):
                a = tier_of(self.news, "https://www.%s/some/story" % left)
                b = tier_of(self.news, "https://www.%s/some/story" % right)
                self.assertEqual(a, b)
                self.assertEqual(a, "2", "papers of record belong on the tier-2 rung")

    def test_they_also_score_and_band_identically(self):
        """Same tier is not enough if some other term separates them -- a
        reader choosing between the two must see the same verdict."""
        for left, right in CO_TIERED_PAIRS:
            with self.subTest(pair=(left, right)):
                a = score(self.news, "https://www.%s/x" % left, "news")
                b = score(self.news, "https://www.%s/x" % right, "news")
                self.assertEqual(a["score"], b["score"])
                self.assertEqual(a["verdict"], b["verdict"])

    def test_cable_networks_are_co_tiered_with_each_other(self):
        """Real desks but mostly panel commentary; splitting them would put a
        political lean in the table."""
        tiers = {d: tier_of(self.news, "https://www.%s/2026/01/01/x" % d)
                 for d in CABLE_NETWORKS}
        self.assertEqual(len(set(tiers.values())), 1, tiers)
        self.assertEqual(set(tiers.values()), {"4"})

    def test_cable_networks_rank_below_the_papers_of_record(self):
        cable = score(self.news, "https://www.cnn.com/2026/01/01/x", "news")["score"]
        for left, right in CO_TIERED_PAIRS:
            for d in (left, right):
                with self.subTest(domain=d):
                    self.assertGreater(
                        score(self.news, "https://www.%s/x" % d, "news")["score"],
                        cable)

    def test_press_release_wires_are_tier_six(self):
        for d in PRESS_RELEASE_WIRES:
            with self.subTest(domain=d):
                self.assertEqual(tier_of(self.news, "https://www.%s/news-release/x" % d),
                                 "6")

    def test_press_release_wires_are_not_citable_and_not_blocked(self):
        """Corporate copy with no editorial gate: 'do not cite', but it is not
        a scraper, so it keeps a tier rather than being blocked."""
        for d in PRESS_RELEASE_WIRES:
            with self.subTest(domain=d):
                r = score(self.news, "https://www.%s/news-release/x" % d, "news")
                self.assertNotIn(r["verdict"], ("PRIMARY", "SUPPORT", "SKIM"))
                self.assertNotEqual(r["verdict"], P.blocked_name(self.news))

    def test_republishing_wrappers_are_blocked(self):
        """The content is someone else's and the originating outlet is the
        citable source -- so these are blocked, not merely demoted."""
        for d in REPUBLISHING_WRAPPERS:
            with self.subTest(domain=d):
                url = "https://www.%s/en-us/news/article-x" % d
                self.assertEqual(tier_of(self.news, url), "block")
                self.assertEqual(score(self.news, url, "news")["verdict"],
                                 P.blocked_name(self.news))

    def test_a_wires_english_service_is_registered_on_its_own_host(self):
        """"an outlet's every host must be listed, or the same story scores
        differently by host." Kyodo's English service lives on a different
        domain from the Japanese wire, so it needs its own entry -- and under
        the English-edition rule it is a translation desk, tier 4 rather than
        the wire's 2. This suite found it unregistered (falling to 5); this is
        the resolved side."""
        self.assertEqual(tier_of(self.news, "https://www.kyodonews.jp/some/story"), "2")
        self.assertEqual(
            tier_of(self.news, "https://english.kyodonews.net/news/2026/01/x.html"),
            "4")
        if ladder_rules_text():
            self.assertIn("english-language edition", ladder_rules_text())

    def test_a_wrapper_is_blocked_only_in_news_mode(self):
        """The block is this mode's reading of a syndication wrapper; it is not
        smuggled into the shared table, where msn.com is simply unregistered."""
        self.assertEqual(tier_of(merged("academic", self.base),
                                 "https://www.msn.com/en-us/news/x"),
                         str(self.base["defaults"]["unregistered_tier"]))


# ---------------------------------------------------------------------------
# Claim 4: community-opinion co-tiers the microblogging hosts
# ---------------------------------------------------------------------------

MICROBLOGS = ["x.com", "twitter.com", "bsky.app", "mastodon.social",
              "fosstodon.org", "hachyderm.io", "threads.net"]


class CommunityMicroblogTests(unittest.TestCase):
    def setUp(self):
        self.base = base_policy()
        self.co = merged("community-opinion", self.base)

    def test_microblogging_hosts_are_all_tier_one(self):
        for d in MICROBLOGS:
            with self.subTest(domain=d):
                self.assertEqual(tier_of(self.co, "https://%s/someone/status/1" % d),
                                 "1")

    def test_no_single_network_is_privileged(self):
        """Listed symmetrically on purpose: a thread on Mastodon is the same
        kind of evidence as the same thread on X."""
        tiers = {d: tier_of(self.co, "https://%s/someone/status/1" % d)
                 for d in MICROBLOGS}
        self.assertEqual(len(set(tiers.values())), 1, tiers)
        bare = {d: score(self.co, "https://%s/someone/status/1" % d, "opinion")["score"]
                for d in MICROBLOGS}
        self.assertEqual(len(set(bare.values())), 1, bare)

    def test_threads_is_co_tiered_with_the_other_microblogs(self):
        """threads.net sat at a lower tier than its peers when this suite was
        written; that is the asymmetry rule (a) exists to prevent, since how
        much practitioner signal a network carries is an engagement question,
        not a tier question. Kept as a regression guard on the resolved side."""
        self.assertEqual(tier_of(self.co, "https://www.threads.net/@someone/post/1"),
                         tier_of(self.co, "https://x.com/someone/status/1"))
        self.assertEqual(str(overrides("community-opinion").get("threads.net")), "1")

    def test_the_mastodon_instances_are_not_folded_into_one_pattern(self):
        """Mastodon has no single host, so each instance must be registered or
        the mode reads only mastodon.social."""
        ov = overrides("community-opinion")
        for d in ("mastodon.social", "fosstodon.org", "hachyderm.io"):
            with self.subTest(domain=d):
                self.assertEqual(str(ov.get(d)), "1")

    def test_github_is_tier_one_in_this_mode(self):
        """Here the issue thread, not the repo, is what gets read -- so github
        joins the discussion venues rather than sitting at the base table's 4
        or non-academic's 3."""
        self.assertEqual(tier_of(self.co, "https://github.com/o/r/issues/42"), "1")
        self.assertEqual(tier_of(merged("academic", self.base),
                                 "https://github.com/o/r/issues/42"), "4")
        self.assertEqual(tier_of(merged("non-academic", self.base),
                                 "https://github.com/o/r/issues/42"), "3")

    def test_microblog_tier_one_still_faces_the_engagement_floor(self):
        """Tier 1 is the venue, not the verdict: a thread nobody read is held
        below the pass mark in this mode."""
        r = score(self.co, "https://x.com/someone/status/1", "opinion",
                  {"hn": {"points": 1}})
        self.assertNotIn(r["verdict"], ("PRIMARY", "SUPPORT"))


# ---------------------------------------------------------------------------
# Claim 5: the Round B additions to the shared academic table
# ---------------------------------------------------------------------------

# Journals of record that were scoring as unregistered before Round B. The
# notes file them at tier 1, "alongside nature/science", which is what the
# tier-1 label describes ("Academic journals, official statistics, standards
# bodies").
SOCIETY_PUBLISHERS = ["journals.aps.org", "pubs.acs.org", "pubs.rsc.org",
                      "iopscience.iop.org", "ams.org", "epubs.siam.org",
                      "pubs.aip.org", "opg.optica.org"]
# Engineering-society and university-press publishers, explicitly "at tier 2".
SOCIETY_TIER_TWO = ["asmedigitalcollection.asme.org", "arc.aiaa.org",
                    "ascelibrary.org", "spiedigitallibrary.org",
                    "pubs.geoscienceworld.org", "psycnet.apa.org",
                    "degruyter.com", "karger.com", "cambridge.org",
                    "journals.uchicago.edu"]
# Field-standard indexes, explicitly "at tier 2".
INDEXES = ["inspirehep.net", "ui.adsabs.harvard.edu", "zbmath.org",
           "mathscinet.ams.org", "openalex.org", "doaj.org"]
# Non-English scholarly infrastructure, registered "at tier 2" for the same reason.
NON_ENGLISH_INFRA = ["jstage.jst.go.jp", "kci.go.kr", "cnki.net"]
# National statistics offices beyond the US/EU/KR set already registered.
STATS_OFFICES = ["destatis.de", "ons.gov.uk", "insee.fr", "stat.go.jp",
                 "statcan.gc.ca", "abs.gov.au"]
PREDATORY = ["hindawi.com", "omicsonline.org", "scirp.org", "waset.org"]


class BaseTableRoundBTests(unittest.TestCase):
    def setUp(self):
        self.base = base_policy()
        self.unregistered = str(self.base["defaults"]["unregistered_tier"])

    def test_society_journals_of_record_are_tier_one(self):
        """The hole Round B filled: physics/chemistry/maths journals of record
        were resolving as unregistered. They sit where the tier-1 label puts
        an academic journal, same rung as nature.com."""
        self.assertEqual(tier_of(self.base, "https://www.nature.com/articles/x"), "1")
        for d in SOCIETY_PUBLISHERS:
            with self.subTest(domain=d):
                self.assertEqual(tier_of(self.base, "https://%s/doi/10.1/x" % d), "1")

    def test_engineering_society_and_university_press_publishers_are_tier_two(self):
        for d in SOCIETY_TIER_TWO:
            with self.subTest(domain=d):
                self.assertEqual(tier_of(self.base, "https://%s/doi/10.1/x" % d), "2")

    def test_field_standard_indexes_are_tier_two(self):
        for d in INDEXES:
            with self.subTest(domain=d):
                self.assertEqual(tier_of(self.base, "https://%s/record/1" % d), "2")

    def test_non_english_scholarly_infrastructure_is_tier_two(self):
        """"an unregistered trace is indistinguishable from no trace" -- so
        J-STAGE/KCI/CNKI are tiered by the same test as their English peers."""
        for d in NON_ENGLISH_INFRA:
            with self.subTest(domain=d):
                self.assertEqual(tier_of(self.base, "https://%s/article/1" % d), "2")

    def test_national_statistics_offices_are_tier_one(self):
        for d in STATS_OFFICES:
            with self.subTest(domain=d):
                self.assertEqual(tier_of(self.base, "https://%s/statistics/x" % d), "1")

    def test_none_of_the_round_b_additions_still_reads_as_unregistered(self):
        for d in (SOCIETY_PUBLISHERS + SOCIETY_TIER_TWO + INDEXES
                  + NON_ENGLISH_INFRA + STATS_OFFICES):
            with self.subTest(domain=d):
                tier, matched = SC.match_tier("https://%s/x" % d, self.base)
                self.assertIsNotNone(matched, "%s is not registered" % d)

    def test_the_additions_reach_a_citable_verdict(self):
        """Registration is the point: each of these must now be usable, which
        is what "scoring as unregistered" cost them."""
        for d in SOCIETY_PUBLISHERS + INDEXES:
            with self.subTest(domain=d):
                self.assertIn(
                    P.verdict_for(self.base,
                                  P.tier_base(self.base,
                                              tier_of(self.base, "https://%s/x" % d))),
                    ("PRIMARY", "SUPPORT"))

    def test_predatory_publishers_are_tier_six(self):
        """A documented systemic peer-review failure, not a mirror."""
        for d in PREDATORY:
            with self.subTest(domain=d):
                self.assertEqual(tier_of(self.base, "https://www.%s/journals/x/" % d),
                                 "6")

    def test_predatory_publishers_are_not_blocked(self):
        """Tier 6 says "do not cite"; block is reserved for scrapers and
        mirrors, which these are not. The distinction is load-bearing: a
        blocked URL is never opened, a tier-6 one can be read and overridden."""
        blocked = [str(p).lower() for p in self.base["domains"]["block"]]
        for d in PREDATORY:
            with self.subTest(domain=d):
                self.assertNotIn(d, blocked)
                r = score(self.base, "https://www.%s/journals/x/" % d, "ai")
                self.assertNotEqual(r["verdict"], P.blocked_name(self.base))
                self.assertEqual(r["verdict"], "DROP")

    def test_predatory_publishers_are_demoted_in_every_mode(self):
        """They live in the shared table, so no mode reads them as citable."""
        for mode in MODES:
            m = merged(mode, self.base)
            for d in PREDATORY:
                with self.subTest(mode=mode, domain=d):
                    self.assertNotIn(
                        P.verdict_for(m, P.tier_base(m, tier_of(m, "https://%s/x" % d))),
                        ("PRIMARY", "SUPPORT"))

    def test_a_parked_lander_is_not_left_registered(self):
        """chromadb.com resolves to a parked page, not Chroma's site, so it was
        removed rather than left registering an ad page as tier 3; trychroma.com
        is the real host and stays."""
        self.assertIsNone(SC.match_tier("https://chromadb.com/", self.base)[1])
        self.assertEqual(tier_of(self.base, "https://www.trychroma.com/"), "3")


if __name__ == "__main__":
    unittest.main()
