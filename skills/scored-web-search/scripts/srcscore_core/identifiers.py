"""Regex identifier extraction (arXiv, DOI, GitHub, PubMed, OpenReview, bio/medRxiv)."""

from __future__ import annotations

import re
import urllib.parse

from .util import age_years, host_path, norm_host

__all__ = [
    "ARXIV_RE", "DOI_RE", "GITHUB_RE", "PUBMED_RE", "OPENREVIEW_RE", "BIORXIV_RE",
    "GITHUB_NON_REPO", "HN_ITEM_RE", "extract_ids", "arxiv_id_age_years",
    "extract_hn_item_id", "extract_person_handle", "fetch_url", "paper_key",
]

ARXIV_RE = re.compile(
    r"arxiv\.org/(?:abs|pdf|html)/([0-9]{4}\.[0-9]{4,5}|[a-z\-]+(?:\.[A-Z]{2})?/[0-9]{7})", re.I)
DOI_RE = re.compile(r"\b(10\.\d{4,9}/[-._;()/:A-Za-z0-9]+)", re.I)
GITHUB_RE = re.compile(r"github\.com/([A-Za-z0-9_.\-]+)/([A-Za-z0-9_.\-]+)")
PUBMED_RE = re.compile(r"pubmed\.ncbi\.nlm\.nih\.gov/(\d+)")
OPENREVIEW_RE = re.compile(r"openreview\.net/(?:forum|pdf)\?id=([A-Za-z0-9_\-]+)")
BIORXIV_RE = re.compile(
    r"(?:bio|med)rxiv\.org/content/(10\.\d{4,9}/[^\s?#]+?)(?:v\d+)?(?:\.full|$|[?#])", re.I)

HN_ITEM_RE = re.compile(r"news\.ycombinator\.com/item\?(?:[^#]*&)?id=(\d+)")

GITHUB_NON_REPO = ("orgs", "features", "about", "topics", "collections", "sponsors")


def extract_hn_item_id(url: str):
    """The HN story id for a thread permalink, or None.

    An HN thread carries the *article's* URL, never its own, so the Algolia
    url-attribute search that `hn_points` runs can never find a thread by its
    permalink. community-opinion mode reads threads as primary sources, so the
    thread's own score has to be reachable -- by object id, not by search.
    """
    m = HN_ITEM_RE.search(url)
    return m.group(1) if m else None


def extract_ids(url: str) -> dict:
    ids = {}
    m = ARXIV_RE.search(url)
    if m:
        raw = m.group(1)
        ids["arxiv"] = re.sub(r"v\d+$", "", raw)
    m = BIORXIV_RE.search(url)
    if m:
        ids["doi"] = m.group(1)
    if "doi" not in ids:
        m = DOI_RE.search(urllib.parse.unquote(url))
        if m:
            ids["doi"] = m.group(1).rstrip(").,;")
    m = GITHUB_RE.search(url)
    if m and m.group(1).lower() not in GITHUB_NON_REPO:
        ids["github"] = (m.group(1), m.group(2).removesuffix(".git"))
    m = PUBMED_RE.search(url)
    if m:
        ids["pmid"] = m.group(1)
    m = OPENREVIEW_RE.search(url)
    if m:
        ids["openreview"] = m.group(1)
    return ids


_ARXIV_DOI_RE = re.compile(r"^10\.48550/arxiv\.(.+)$", re.I)


def paper_key(url: str):
    """The paper a URL is a copy of, from identifiers in the URL alone, or None.

    arXiv abs/pdf/html pages and versions, and the arXiv DOI, are one paper; so are
    a doi.org link and a publisher page carrying the same DOI. Pattern-only: a page
    with no identifier in its URL (a Semantic Scholar page) is never grouped.
    """
    ids = extract_ids(url)
    if "arxiv" in ids:
        return "arxiv:" + ids["arxiv"].lower()
    if "doi" in ids:
        doi = ids["doi"].lower()
        m = _ARXIV_DOI_RE.match(doi)
        return ("arxiv:" + re.sub(r"v\d+$", "", m.group(1))) if m else "doi:" + doi
    if "pmid" in ids:
        return "pmid:" + ids["pmid"]
    return None


_ARXIV_NEW_ID_RE = re.compile(r"^(\d{2})(\d{2})\.\d{4,5}$")
_ARXIV_OLD_ID_RE = re.compile(r"^[a-z\-]+(?:\.[A-Z]{2})?/(\d{2})(\d{2})\d{3}$", re.I)


def arxiv_id_age_years(arxiv_id: str):
    """Decode the YYMM submission month baked into an arXiv id - both the
    2007+ `YYMM.NNNNN` scheme and the pre-2007 `archive.subj-class/YYMMNNN`
    scheme embed it - and return the paper's age in years with no network
    call. Returns None if `arxiv_id` doesn't match either scheme.
    """
    if not arxiv_id:
        return None
    m = _ARXIV_NEW_ID_RE.match(arxiv_id)
    old = False
    if not m:
        m = _ARXIV_OLD_ID_RE.match(arxiv_id)
        old = True
    if not m:
        return None
    yy, mm = int(m.group(1)), int(m.group(2))
    if not 1 <= mm <= 12:
        return None
    year = 1900 + yy if old and yy >= 91 else 2000 + yy
    return age_years("%04d-%02d-01" % (year, mm), None)


_X_PROFILE_RE = re.compile(r"^/([A-Za-z0-9_]{1,15})(?:/|$)")
_X_NON_PROFILE_PATHS = frozenset((
    "i", "home", "search", "hashtag", "explore", "notifications",
    "messages", "settings", "compose",
))
_REDDIT_USER_RE = re.compile(r"^/(?:u|user)/([A-Za-z0-9_\-]+)", re.I)
_PERSON_HOSTS = frozenset(("x.com", "twitter.com", "reddit.com"))


def extract_person_handle(url: str):
    """Return (host, handle) for a known social-profile URL, else None.

    Pattern-only, same as domain tiering - no network identity lookup. Only
    covers platforms where the author's handle is actually present in the
    URL path (x.com/twitter.com profile & status links, reddit.com/u/...
    profile links); a reddit discussion-thread URL never carries the
    author's name, so it can't be matched this way.
    """
    host = norm_host(url)
    if host not in _PERSON_HOSTS:
        return None
    path = host_path(url)[len(host):]
    if host in ("x.com", "twitter.com"):
        m = _X_PROFILE_RE.match(path)
        if m and m.group(1).lower() not in _X_NON_PROFILE_PATHS:
            return host, m.group(1).lower()
    elif host == "reddit.com":
        m = _REDDIT_USER_RE.match(path)
        if m:
            return host, m.group(1).lower()
    return None


_PMC_OLD_RE = re.compile(r"^https?://(?:www\.)?ncbi\.nlm\.nih\.gov/pmc/(articles/PMC\d+.*)$", re.I)
_ARXIV_PAGE_RE = re.compile(
    r"^https?://(?:www\.)?arxiv\.org/(?:abs|pdf)/((\d{2})(\d{2})\.\d{4,5}(?:v\d+)?)(?:\.pdf)?/?(?:[?#].*)?$", re.I)
# arXiv renders HTML only for submissions from December 2023 on.
_ARXIV_HTML_FROM = (23, 12)


def fetch_url(url: str) -> str:
    """The address WebFetch should be given for `url`; `url` itself unless a rule applies.

    WebFetch does not follow cross-host redirects, so an old PMC link
    (www.ncbi.nlm.nih.gov/pmc/...) dead-ends on a redirect notice. arXiv
    abs/pdf pages read as an abstract or a PDF; the HTML rendering is the
    full text, but it is missing for older submissions, hence the cut-off.
    Pattern-only: no request is made.
    """
    m = _PMC_OLD_RE.match(url)
    if m:
        return "https://pmc.ncbi.nlm.nih.gov/" + m.group(1)
    m = _ARXIV_PAGE_RE.match(url)
    if m and (int(m.group(2)), int(m.group(3))) >= _ARXIV_HTML_FROM:
        return "https://arxiv.org/html/" + m.group(1)
    return url
