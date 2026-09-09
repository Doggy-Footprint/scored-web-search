"""Policy loading, validation, mode-overlay merging and CLI-driven tuning.

srcscore.py reads ONLY what this module hands back for scoring behaviour;
nothing about scoring is hard-coded in srcscore_core/scoring.py. The base
policy (scripts/policy.json) is the single source of truth for domain tiers
and verdict bands. A `--mode` overlay (scripts/modes/*.json) may override
weights/switches on top of it, and validation always runs last against the
final merged result - there is no silent fallback anywhere in this chain,
same as the original single-file scorer.
"""

from __future__ import annotations

import json
import os
import re

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))  # scripts/
POLICY_PATH = os.environ.get("SRCSCORE_POLICY", os.path.join(HERE, "policy.json"))
MODES_DIR = os.environ.get("SRCSCORE_MODES_DIR", os.path.join(HERE, "modes"))

__all__ = [
    "PolicyError", "REQUIRED_KEYS", "POLICY_PATH", "MODES_DIR",
    "load_policy", "validate_policy", "tier_base", "verdict_for", "blocked_name",
    "deep_merge", "load_mode_overlay", "apply_mode", "apply_domain_overrides",
    "signal_enabled",
]


class PolicyError(RuntimeError):
    """policy.json is missing, unreadable or structurally invalid."""


REQUIRED_KEYS = (
    "defaults", "tiers", "verdicts", "field_halflife_years", "citations",
    "recency", "peer_review", "citation_gap", "engagement", "penalties",
    "seo_path_patterns", "preprint_hosts", "domains",
)

DEFAULT_SIGNALS = {"recency_decay": True, "peer_review": True, "engagement": True}


# ----------------------------------------------------------------------------
# Load / validate
# ----------------------------------------------------------------------------

def load_policy(path: str = None) -> dict:
    """Read policy.json. Raises PolicyError - there is no silent fallback:
    scoring with a guessed policy would be worse than not scoring at all."""
    path = path or POLICY_PATH
    try:
        with open(path, "r", encoding="utf-8") as f:
            p = json.load(f)
    except OSError as e:
        raise PolicyError("cannot read policy file %s: %s" % (path, e))
    except ValueError as e:
        raise PolicyError("policy file %s is not valid JSON: %s" % (path, e))
    validate_policy(p, path)
    return p


def validate_policy(p: dict, path: str = "policy") -> None:
    """Structural sanity check. Cheap, and it turns a silent mis-score into a
    loud failure at load time."""
    def bad(msg):
        raise PolicyError("%s: %s" % (path, msg))

    if not isinstance(p, dict):
        bad("top level must be an object")
    for k in REQUIRED_KEYS:
        if k not in p:
            bad("missing required key %r" % k)

    tiers = p["tiers"]
    for name, spec in tiers.items():
        if not isinstance(spec, dict) or not isinstance(spec.get("base"), (int, float)):
            bad("tiers.%s needs a numeric 'base'" % name)
    if "block" not in tiers:
        bad("tiers must define 'block'")

    bands = (p["verdicts"] or {}).get("bands") or []
    if not bands:
        bad("verdicts.bands is empty")
    mins = [b.get("min") for b in bands]
    if any(not isinstance(m, (int, float)) for m in mins):
        bad("every verdict band needs a numeric 'min'")
    if mins != sorted(mins, reverse=True):
        bad("verdicts.bands must be ordered from highest 'min' to lowest")
    if mins[-1] != 0:
        bad("the lowest verdict band must start at 0")

    dom = p["domains"]
    seen = {}
    for tier_name, patterns in dom.items():
        if tier_name not in tiers:
            bad("domains.%s has no matching tier definition" % tier_name)
        if not isinstance(patterns, list):
            bad("domains.%s must be a list" % tier_name)
        for pat in patterns:
            key = str(pat).lower()
            if key in seen:
                bad("domain %r listed in both %s and %s" % (pat, seen[key], tier_name))
            seen[key] = tier_name

    default_tier = str((p["defaults"] or {}).get("unregistered_tier", ""))
    if default_tier not in tiers:
        bad("defaults.unregistered_tier %r has no tier definition" % default_tier)
    if (p["defaults"] or {}).get("field") not in p["field_halflife_years"]:
        bad("defaults.field is not present in field_halflife_years")

    signals = p.get("signals", DEFAULT_SIGNALS)
    if not isinstance(signals, dict) or any(
            not isinstance(signals.get(k, True), bool) for k in DEFAULT_SIGNALS):
        bad("signals.* must be booleans")

    ov = p.get("domain_overrides")
    if ov is not None:
        if not isinstance(ov, dict):
            bad("domain_overrides must be an object")
        for pat, tier_name in ov.items():
            if str(tier_name) not in tiers:
                bad("domain_overrides[%r] targets tier %r, which has no tier definition"
                    % (pat, tier_name))

    floor = p.get("engagement_floor")
    if floor is not None:
        if not isinstance(floor, dict):
            bad("engagement_floor must be an object")
        for k in ("min_points", "penalty"):
            if not isinstance(floor.get(k), (int, float)):
                bad("engagement_floor needs a numeric %r" % k)
        if not isinstance(floor.get("tiers"), list):
            bad("engagement_floor needs a 'tiers' list")
        for t in floor["tiers"]:
            if str(t) not in tiers:
                bad("engagement_floor.tiers references unknown tier %r" % t)

    vp = p.get("penalties", {}).get("version_path")
    if vp is not None:
        if not isinstance(vp.get("points"), (int, float)):
            bad("penalties.version_path needs numeric 'points'")
        if not isinstance(p.get("version_path_patterns"), list):
            bad("penalties.version_path is set but version_path_patterns is not a list")
        rx = p.get("version_path_regex", "")
        if not isinstance(rx, str):
            bad("version_path_regex must be a string")
        if rx:
            try:
                if re.compile(rx).groups != 1:
                    bad("version_path_regex must have exactly one capture group "
                        "(the version string)")
            except re.error as e:
                bad("version_path_regex is not a valid regex: %s" % e)

    dv = p.get("doc_versions")
    if dv is not None:
        if not isinstance(dv, dict) or any(
                not isinstance(v, str) or not v for v in dv.values()):
            bad("doc_versions must map a domain to a non-empty version string")


def tier_base(policy: dict, tier: str) -> float:
    return float(policy["tiers"][tier]["base"])


def verdict_for(policy: dict, score: float) -> str:
    for band in policy["verdicts"]["bands"]:
        if score >= band["min"]:
            return band["name"]
    return policy["verdicts"]["bands"][-1]["name"]


def blocked_name(policy: dict) -> str:
    return policy["verdicts"].get("blocked_name", "BLOCKED")


def signal_enabled(policy: dict, name: str) -> bool:
    """name is one of 'recency_decay', 'peer_review', 'engagement'."""
    return bool(policy.get("signals", DEFAULT_SIGNALS).get(name, True))


# ----------------------------------------------------------------------------
# Mode overlays
# ----------------------------------------------------------------------------

def deep_merge(base: dict, overlay: dict) -> dict:
    """Recursively merge `overlay` onto a deep copy of `base`. Nested dicts
    merge key-by-key; any other value (including lists) is fully replaced by
    the overlay's value."""
    out = dict(base)
    for k, v in overlay.items():
        if k == "_readme":
            continue
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = deep_merge(out[k], v)
        else:
            out[k] = v
    return out


def load_mode_overlay(mode: str, modes_dir: str = None) -> dict:
    modes_dir = modes_dir or MODES_DIR
    fname = mode.replace("-", "_") + ".json"
    path = os.path.join(modes_dir, fname)
    try:
        with open(path, "r", encoding="utf-8") as f:
            overlay = json.load(f)
    except OSError as e:
        raise PolicyError("cannot read mode file %s: %s" % (path, e))
    except ValueError as e:
        raise PolicyError("mode file %s is not valid JSON: %s" % (path, e))
    if not isinstance(overlay, dict):
        raise PolicyError("mode file %s: top level must be an object" % path)
    return overlay


def apply_domain_overrides(policy: dict) -> dict:
    """Move the domains named in `policy["domain_overrides"]` to the tier they
    name, removing them from whatever tier the base policy filed them under.

    A mode is a different question, not a discount on the same one: reddit is a
    tier-5 aggregator when the question is scholarly and a tier-1 primary source
    when the question is what practitioners actually hit. Expressing that as a
    remap rather than as an overlay `domains` block matters twice over --
    `deep_merge` replaces lists wholesale, so redefining a tier list would delete
    the base entries, and `validate_policy` rejects a domain filed under two
    tiers, so the old entry has to go. The result is an ordinary `domains` dict,
    which is why `match_tier` needs no knowledge of any of this.
    """
    overrides = policy.get("domain_overrides")
    if not overrides:
        return policy
    wanted = {str(pat).lower(): str(tier) for pat, tier in overrides.items()}
    domains = {tier: list(pats) for tier, pats in policy["domains"].items()}
    for tier_name, patterns in domains.items():
        domains[tier_name] = [p for p in patterns if str(p).lower() not in wanted]
    for pat, tier_name in overrides.items():
        domains.setdefault(str(tier_name), []).append(pat)
    out = dict(policy)
    out["domains"] = domains
    return out


def apply_mode(policy: dict, mode: str, modes_dir: str = None) -> dict:
    overlay = load_mode_overlay(mode, modes_dir)
    return apply_domain_overrides(deep_merge(policy, overlay))
