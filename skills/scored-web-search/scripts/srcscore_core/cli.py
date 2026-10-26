"""argparse wiring + main() for the srcscore CLI."""

from __future__ import annotations

import argparse
import json
import sys

from .fetchers import CACHE_DIR, LOOKUP_HOSTS, STATS, Cache
from .io_format import parse_input, render_compact, render_md, render_table
from .policy import PolicyError, apply_mode, load_policy, validate_policy
from .scoring import score_many

__all__ = ["main"]

VERSION = "2.0.0"

MODES = ("academic", "non-academic", "community-opinion", "news", "official-docs")


def main(argv=None):
    ap = argparse.ArgumentParser(
        description="Deterministic source credibility scorer (zero LLM tokens).")
    ap.add_argument("--in", dest="infiles", action="append", default=[],
                    help="input file (repeatable, records are concatenated; default: stdin)")
    ap.add_argument("-u", "--url", action="append", default=[],
                    help="score this URL (repeatable)")
    ap.add_argument("--format", choices=["table", "compact", "json", "md", "urls"], default="table",
                    help="compact: PRIMARY/SUPPORT rows and a count per verdict")
    ap.add_argument("--full-out", default=None, metavar="PATH",
                    help="also write the full table to PATH (for --format compact)")
    ap.add_argument("--min", type=float, default=None, help="drop anything below this score")
    ap.add_argument("--top", type=int, default=None, help="keep only the top N")
    ap.add_argument("--field", default=None,
                    help="research field, sets the citation half-life (see policy.json)")
    ap.add_argument("--no-net", action="store_true",
                    help="offline testing only: no citation, star or HN lookups, so the scores "
                         "are not what a research run should act on")
    ap.add_argument("--workers", type=int, default=None)
    ap.add_argument("--policy", default=None, help="path to policy.json")
    ap.add_argument("--mode", choices=MODES, default="academic",
                    help="scoring profile, loaded from scripts/modes/<mode>.json "
                         "and merged onto --policy (default: academic)")
    ap.add_argument("--doc-version", action="append", default=[], metavar="DOMAIN=VERSION",
                    help="the documentation version you are on, e.g. nextjs.org=14 "
                         "(repeatable). Any other version of that domain's docs is "
                         "penalised as the wrong manual; pages with no version in the "
                         "path are left alone. Without this, only a bare major-version "
                         "path (/docs/13/) is read as superseded.")
    ap.add_argument("--doc-variant", action="append", default=[], metavar="DOMAIN=VARIANT",
                    help="the docs variant the question is about, e.g. nextjs.org=app "
                         "(repeatable; variants are listed in policy.json doc_variants). "
                         "Pages under another variant of that domain are penalised.")
    ap.add_argument("--official-domain", action="append", default=[], metavar="DOMAIN",
                    help="official-docs only: the product's own docs host, e.g. biomejs.dev "
                         "(repeatable). An unregistered host (and its subdomains) is scored "
                         "at the mode's official_domain tier and flagged; registered hosts "
                         "keep their tier.")
    ap.add_argument("--modes-dir", default=None, help="override scripts/modes/ location")
    ap.add_argument("--version", action="version", version=VERSION)
    a = ap.parse_args(argv)

    try:
        policy = load_policy(a.policy)
        policy = apply_mode(policy, a.mode, a.modes_dir)
        validate_policy(policy, "merged policy (%s + --mode %s)" % (a.policy or "policy.json",
                                                                      a.mode))
    except PolicyError as e:
        print("srcscore: %s" % e, file=sys.stderr)
        return 3

    doc_versions = {}
    for spec in a.doc_version:
        dom, sep, ver = spec.partition("=")
        if not sep or not dom.strip() or not ver.strip():
            print("srcscore: --doc-version expects DOMAIN=VERSION, got %r" % spec,
                  file=sys.stderr)
            return 2
        doc_versions[dom.strip().lower()] = ver.strip()
    if doc_versions:
        policy = dict(policy, doc_versions=doc_versions)
        try:
            validate_policy(policy, "merged policy + --doc-version")
        except PolicyError as e:
            print("srcscore: %s" % e, file=sys.stderr)
            return 3

    doc_variant = {}
    for spec in a.doc_variant:
        dom, sep, var = spec.partition("=")
        if not sep or not dom.strip() or not var.strip():
            print("srcscore: --doc-variant expects DOMAIN=VARIANT, got %r" % spec,
                  file=sys.stderr)
            return 2
        doc_variant[dom.strip().lower()] = var.strip().lower()
    if doc_variant:
        policy = dict(policy, doc_variant=doc_variant)
        try:
            validate_policy(policy, "merged policy + --doc-variant")
        except PolicyError as e:
            print("srcscore: %s" % e, file=sys.stderr)
            return 3

    official = [d.strip().lower() for d in a.official_domain]
    if official:
        if not all(official):
            print("srcscore: --official-domain expects a domain", file=sys.stderr)
            return 2
        if not policy.get("official_domain"):
            print("srcscore: --official-domain is not available in --mode %s" % a.mode,
                  file=sys.stderr)
            return 2
        policy = dict(policy, official_domains=official)
        try:
            validate_policy(policy, "merged policy + --official-domain")
        except PolicyError as e:
            print("srcscore: %s" % e, file=sys.stderr)
            return 3

    field = a.field or policy["defaults"]["field"]
    if field not in policy["field_halflife_years"]:
        print("srcscore: unknown --field %r (known: %s)"
              % (field, ", ".join(sorted(policy["field_halflife_years"]))), file=sys.stderr)
        return 2

    items = [{"url": u, "title": ""} for u in a.url]
    if not items:
        for infile in a.infiles or ["-"]:
            try:
                raw = sys.stdin.read() if infile == "-" else open(infile, encoding="utf-8").read()
            except OSError as e:
                print("srcscore: cannot read input: %s" % e, file=sys.stderr)
                return 2
            items += parse_input(raw)
    if not items:
        print("srcscore: no URLs in input", file=sys.stderr)
        return 2

    cache = Cache(CACHE_DIR, float(policy["defaults"].get("cache_ttl_days", 14)))
    workers = a.workers or int(policy["defaults"].get("workers", 8))
    rows = score_many(items, policy, cache, field, not a.no_net, workers)
    cache.flush()

    # A blocked or offline API would otherwise degrade every academic source to
    # its bare domain tier without saying so. Fail loudly instead.
    if STATS["failed"]:
        msg = ("srcscore: %d of %d external lookups could not be reached"
               % (STATS["failed"], STATS["lookups"]))
        if STATS["failed"] * 2 >= STATS["lookups"]:
            print("%s.\nScores would rest on the domain tier alone and mislead. Allow network "
                  "access to %s, then run again." % (msg, ", ".join(LOOKUP_HOSTS)), file=sys.stderr)
            return 4
        print("%s; those rows are flagged `lookup-failed`." % msg, file=sys.stderr)

    rows.sort(key=lambda r: -r["score"])
    if a.min is not None:
        rows = [r for r in rows if r["score"] >= a.min]
    if a.top:
        rows = rows[: a.top]

    if a.full_out:
        try:
            with open(a.full_out, "w", encoding="utf-8") as f:
                f.write(render_table(rows) + "\n")
        except OSError as e:
            print("srcscore: cannot write --full-out: %s" % e, file=sys.stderr)
            return 2

    if a.format == "json":
        print(json.dumps(rows, ensure_ascii=False, indent=1))
    elif a.format == "md":
        print(render_md(rows))
    elif a.format == "compact":
        print(render_compact(rows))
    elif a.format == "urls":
        for r in rows:
            print(r["fetch_url"])
    else:
        print(render_table(rows))
    return 0
