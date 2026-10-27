"""Independent tests for issue 5: --official-domain, non-academic / community
overlay additions, and the matching docs."""

import copy
import json
import os
import subprocess
import sys
import unittest

import _pathsetup  # noqa: F401
from srcscore_core import policy as P
from srcscore_core import scoring as SC

ROOT = _pathsetup.ROOT
SCRIPTS = _pathsetup.SCRIPTS
SRCSCORE = os.path.join(SCRIPTS, "srcscore.py")
CHECK_POLICY = os.path.join(ROOT, "scripts", "check_policy.py")
SKILL_MD = os.path.join(ROOT, "skills", "scored-web-search", "SKILL.md")
OFFICIAL_JSON = os.path.join(SCRIPTS, "modes", "official_docs.json")


def run(*args):
    return subprocess.run([sys.executable, SRCSCORE, "--no-net", "--format", "json", *args],
                          capture_output=True, text=True)


def score_urls(urls, mode="official-docs", domains=()):
    args = ["--mode", mode]
    for d in domains:
        args += ["--official-domain", d]
    for u in urls:
        args += ["-u", u]
    p = run(*args)
    if p.returncode != 0:
        raise AssertionError("srcscore exit %d: %s" % (p.returncode, p.stderr))
    return {r["url"]: r for r in json.loads(p.stdout)}


def tags(r):
    return set(r.get("flags", [])) | set(r.get("signals", []))


def merged(mode):
    return P.apply_mode(P.load_policy(), mode)


def tier_of(policy, url):
    return SC.match_tier(url, policy)[0]


# ---------------------------------------------------------------------------
# 1. CLI flag
# ---------------------------------------------------------------------------

class OfficialDomainCliTests(unittest.TestCase):
    def test_rejected_in_other_modes(self):
        for mode in ["academic", "non-academic", "community-opinion", "news"]:
            p = run("--mode", mode, "--official-domain", "biomejs.dev", "-u", "https://biomejs.dev/")
            self.assertEqual(p.returncode, 2, (mode, p.stdout, p.stderr))
            self.assertTrue(p.stderr.strip(), mode)

    def test_rejected_default_mode(self):
        p = run("--official-domain", "biomejs.dev", "-u", "https://biomejs.dev/")
        self.assertEqual(p.returncode, 2, p.stderr)
        self.assertTrue(p.stderr.strip())

    def test_empty_domain(self):
        for v in ["", "   "]:
            p = run("--mode", "official-docs", "--official-domain", v, "-u", "https://biomejs.dev/")
            self.assertEqual(p.returncode, 2, (repr(v), p.stderr))

    def test_unregistered_baseline_tier5(self):
        r = score_urls(["https://biomejs.dev/guides/"])["https://biomejs.dev/guides/"]
        self.assertEqual(r["tier"], "5")
        self.assertNotIn("official-claimed", tags(r))

    def test_claimed_host_and_subdomain(self):
        urls = ["https://biomejs.dev/guides/getting-started/",
                "https://next.biomejs.dev/reference/cli/"]
        res = score_urls(urls, domains=["biomejs.dev"])
        for u in urls:
            r = res[u]
            self.assertEqual(r["tier"], "2", u)
            self.assertIn("official-claimed", tags(r), u)
            self.assertEqual(r["score"], 74.0, u)
            self.assertEqual(r["verdict"], "SUPPORT", u)

    def test_case_insensitive_flag_value(self):
        r = score_urls(["https://biomejs.dev/guides/"], domains=["BiomeJS.DEV"])["https://biomejs.dev/guides/"]
        self.assertEqual(r["tier"], "2")
        self.assertIn("official-claimed", tags(r))

    def test_no_dot_boundary_not_claimed(self):
        r = score_urls(["https://notbiomejs.dev/guides/"], domains=["biomejs.dev"])["https://notbiomejs.dev/guides/"]
        self.assertEqual(r["tier"], "5")
        self.assertNotIn("official-claimed", tags(r))

    def test_other_unregistered_host_untouched(self):
        r = score_urls(["https://example-unregistered.io/docs/"], domains=["biomejs.dev"])[
            "https://example-unregistered.io/docs/"]
        self.assertEqual(r["tier"], "5")
        self.assertNotIn("official-claimed", tags(r))

    def test_repeatable(self):
        res = score_urls(["https://biomejs.dev/a/", "https://oxc.rs/docs/"],
                         domains=["biomejs.dev", "oxc.rs"])
        for r in res.values():
            self.assertEqual(r["tier"], "2", r["url"])
            self.assertIn("official-claimed", tags(r))

    def test_registered_hosts_keep_tier(self):
        urls = ["https://vercel.com/templates", "https://docs.python.org/3/library/os.html"]
        base = score_urls(urls)
        claimed = score_urls(urls, domains=["vercel.com", "python.org", "docs.python.org"])
        self.assertEqual(base["https://vercel.com/templates"]["tier"], "3")
        self.assertEqual(base["https://docs.python.org/3/library/os.html"]["tier"], "1")
        for u in urls:
            self.assertEqual(claimed[u]["tier"], base[u]["tier"], u)
            self.assertEqual(claimed[u]["score"], base[u]["score"], u)
            self.assertNotIn("official-claimed", tags(claimed[u]), u)

    def test_path_penalty_still_applies(self):
        u = "https://biomejs.dev/blog/some-post/"
        r = score_urls([u], domains=["biomejs.dev"])[u]
        self.assertEqual(r["tier"], "2")
        self.assertIn("official-claimed", tags(r))
        self.assertIn("off-reference-path", tags(r))
        self.assertEqual(r["score"], 74.0 - 28.0)

    def test_without_flag_unchanged(self):
        urls = ["https://biomejs.dev/guides/", "https://vercel.com/templates",
                "https://docs.python.org/3/"]
        a = score_urls(urls)
        b = score_urls(urls, domains=["unrelated-domain.example"])
        for u in urls:
            self.assertEqual(a[u]["tier"], b[u]["tier"])
            self.assertEqual(a[u]["score"], b[u]["score"])


# ---------------------------------------------------------------------------
# 2. Policy data + validation
# ---------------------------------------------------------------------------

class OfficialDomainPolicyTests(unittest.TestCase):
    def test_overlay_has_config(self):
        with open(OFFICIAL_JSON) as f:
            od = json.load(f)["official_domain"]
        self.assertEqual(od["tier"], "2")
        self.assertEqual(od["flag"], "official-claimed")

    def test_merged_policy_valid(self):
        P.validate_policy(merged("official-docs"))
        p = dict(merged("official-docs"), official_domains=["biomejs.dev"])
        P.validate_policy(p)

    def test_unknown_tier_rejected(self):
        p = copy.deepcopy(merged("official-docs"))
        p["official_domain"]["tier"] = "99"
        with self.assertRaises(P.PolicyError):
            P.validate_policy(p)

    def test_official_domains_without_config_rejected(self):
        p = copy.deepcopy(merged("academic"))
        p.pop("official_domain", None)
        p["official_domains"] = ["biomejs.dev"]
        with self.assertRaises(P.PolicyError):
            P.validate_policy(p)

    def test_official_domains_shape_rejected(self):
        for bad in ["biomejs.dev", [""], [1], ["ok", None], {"a": "b"}]:
            p = copy.deepcopy(merged("official-docs"))
            p["official_domains"] = bad
            with self.assertRaises(P.PolicyError, msg=repr(bad)):
                P.validate_policy(p)

    def test_check_policy_exits_0(self):
        p = subprocess.run([sys.executable, CHECK_POLICY], capture_output=True, text=True, cwd=ROOT)
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)


# ---------------------------------------------------------------------------
# 3. Mode overlays
# ---------------------------------------------------------------------------

NA_TIER2 = ["severalnines.com", "dropbox.tech", "airbnb.tech", "engineering.atspotify.com",
            "engineering.linkedin.com", "eng.lyft.com", "medium.com/pinterest-engineering",
            "etsy.com/codeascraft", "engineering.zalando.com", "blog.booking.com",
            "engineering.grab.com", "engineering.linecorp.com", "tech.kakao.com", "d2.naver.com",
            "techblog.woowahan.com", "toss.tech", "canva.dev", "hillelwayne.com", "ferd.ca",
            "matklad.github.io", "fasterthanli.me", "blog.regehr.org", "lemire.me", "aphyr.com",
            "mrale.ph", "nullprogram.com", "evanmiller.org", "kalzumeus.com", "apenwarr.ca",
            "drmaciver.com"]
NA_TIER3 = ["datadoghq.com", "honeycomb.io", "fly.io", "tailscale.com", "sentry.engineering",
            "planetscale.com", "discord.com/blog", "figma.com/blog"]


def as_url(pat, tail="some-article"):
    return "https://" + pat.rstrip("/") + "/" + tail


class OverlayTests(unittest.TestCase):
    def test_non_academic_tier2(self):
        p = merged("non-academic")
        for pat in NA_TIER2:
            self.assertEqual(tier_of(p, as_url(pat)), "2", pat)

    def test_non_academic_tier3(self):
        p = merged("non-academic")
        for pat in NA_TIER3:
            self.assertEqual(tier_of(p, as_url(pat)), "3", pat)

    def test_non_academic_no_tier4_overrides(self):
        ov = P.load_mode_overlay("non-academic").get("domain_overrides") or {}
        fours = [k for k, v in ov.items() if not k.startswith("_") and str(v) == "4"]
        self.assertEqual(fours, [])

    def test_path_scoped_no_leak(self):
        """Other paths on a host with a path-scoped entry resolve exactly as
        they would without that entry (i.e. the base/academic resolution of the
        host plus any host-level overlay)."""
        na = merged("non-academic")
        ac = merged("academic")
        cases = {"medium.com/pinterest-engineering": "https://medium.com/someone-else/post",
                 "etsy.com/codeascraft": "https://etsy.com/listing/123",
                 "discord.com/blog": "https://discord.com/channels/1",
                 "figma.com/blog": "https://figma.com/design/abc"}
        for scoped, other in cases.items():
            t, pat = SC.match_tier(other, na)
            self.assertNotEqual(pat, scoped, other)
            stripped = copy.deepcopy(na)
            for tn, pats in stripped["domains"].items():
                stripped["domains"][tn] = [x for x in pats if str(x).lower() != scoped]
            self.assertEqual(t, tier_of(stripped, other), other)
        self.assertEqual(tier_of(na, "https://medium.com/someone-else/post"),
                         tier_of(ac, "https://medium.com/someone-else/post"))

    def test_community_opinion(self):
        p = merged("community-opinion")
        self.assertEqual(tier_of(p, "https://addyo.substack.com/p/x"), "2")
        self.assertNotEqual(tier_of(p, "https://someoneelse.substack.com/p/x"), "2")
        self.assertEqual(tier_of(p, "https://rubyevents.org/talks/x"), "2")
        t, pat = SC.match_tier("https://buttondown.com/someone/archive/x", p)
        self.assertIsNone(pat)
        self.assertEqual(t, "5")


# ---------------------------------------------------------------------------
# 5. Docs
# ---------------------------------------------------------------------------

def read(path):
    with open(path, encoding="utf-8") as f:
        return f.read()


def section(text, step):
    """Text from the heading that mentions `Step <step>` to the next heading of
    the same or higher level that mentions another step."""
    lines = text.splitlines()
    start = None
    for i, ln in enumerate(lines):
        if ln.lstrip().startswith("#") and ("Step %s" % step) in ln:
            start = i
            level = len(ln) - len(ln.lstrip("#"))
            break
    if start is None:
        return ""
    out = [lines[start]]
    for ln in lines[start + 1:]:
        if ln.startswith("#") and len(ln) - len(ln.lstrip("#")) <= level:
            break
        out.append(ln)
    return "\n".join(out)


class DocsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.skill = read(SKILL_MD)

    def test_step1_site_queries(self):
        s1 = section(self.skill, "1")
        self.assertTrue(s1, "no Step 1 heading")
        self.assertIn("site:", s1)
        self.assertIn("official-docs", s1)

    def test_step2_official_domain(self):
        s2 = section(self.skill, "2")
        self.assertTrue(s2, "no Step 2 heading")
        self.assertIn("--official-domain", s2)

    def test_step5_verify_academic_and_tracing(self):
        s5 = section(self.skill, "5")
        self.assertTrue(s5, "no Step 5 heading")
        low = s5.lower()
        self.assertIn("academic", low)
        self.assertIn("always", low)
        self.assertTrue("trac" in low and "original" in low, s5[:500])
        self.assertIn("round", low)


if __name__ == "__main__":
    unittest.main()
