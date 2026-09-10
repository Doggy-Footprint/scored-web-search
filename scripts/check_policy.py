#!/usr/bin/env python3
"""
check_policy - keep policy.json, the mode overlays and the scorer in agreement.

Two rule-based checks, no LLM involved:

  1. schema     policy.json (and every scripts/modes/*.json overlay merged
                onto it) loads, is structurally valid, has no duplicate
                domains and has properly ordered verdict bands.
  2. modes      Every scripts/modes/*.json overlay merges onto the base
                policy and still validates.

USAGE
-----
  python3 scripts/check_policy.py            # verify everything (exit 1 on failure)
  python3 scripts/check_policy.py --only schema
  python3 scripts/check_policy.py --only modes

Installed as a pre-commit hook by scripts/install-hooks.sh.
"""

from __future__ import annotations

import argparse
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)

import srcscore as S  # noqa: E402

MODES_DIR = os.path.join(HERE, "modes")


# ----------------------------------------------------------------------------
# Checks
# ----------------------------------------------------------------------------

def check_schema(policy_path):
    try:
        policy = S.load_policy(policy_path)
    except S.PolicyError as e:
        return None, ["schema: %s" % e]
    return policy, []


def check_modes(policy, modes_dir):
    """Every mode overlay must merge onto the base policy and still validate."""
    problems = []
    if not os.path.isdir(modes_dir):
        return ["modes: no such directory %s" % modes_dir]
    for fname in sorted(os.listdir(modes_dir)):
        if not fname.endswith(".json"):
            continue
        mode = fname[: -len(".json")]
        path = os.path.join(modes_dir, fname)
        try:
            merged = S.apply_mode(policy, mode, modes_dir)
            S.validate_policy(merged, "modes/%s" % fname)
        except S.PolicyError as e:
            problems.append("modes: %s" % e)
        except OSError as e:
            problems.append("modes: cannot read %s: %s" % (path, e))
    return problems


def main(argv=None):
    ap = argparse.ArgumentParser(description="Verify policy.json against the mode overlays.")
    ap.add_argument("--policy", default=S.POLICY_PATH)
    ap.add_argument("--modes-dir", default=MODES_DIR)
    ap.add_argument("--only", choices=["schema", "modes"], action="append", default=[])
    ap.add_argument("-q", "--quiet", action="store_true")
    a = ap.parse_args(argv)
    only = set(a.only) or {"schema", "modes"}

    base_policy, problems = check_schema(a.policy)
    if problems:
        for p in problems:
            print(p, file=sys.stderr)
        return 1

    if "modes" in only:
        problems += check_modes(base_policy, a.modes_dir)

    if problems:
        for p in problems:
            print(p, file=sys.stderr)
        print("\ncheck_policy: FAILED (%d problem%s)."
              % (len(problems), "" if len(problems) == 1 else "s"), file=sys.stderr)
        return 1

    if not a.quiet:
        print("check_policy: ok (%d domains, %d modes)"
              % (sum(len(v) for v in base_policy["domains"].values()),
                 len([f for f in os.listdir(a.modes_dir) if f.endswith(".json")])))
    return 0


if __name__ == "__main__":
    sys.exit(main())
