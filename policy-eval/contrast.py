#!/usr/bin/env python3
"""Join a blind judge run against srcscore output and report where the policy disagrees.

Judge labels (useful/marginal/junk) are produced with no access to the policy; see
METHODOLOGY.md. This script only joins and counts -- it makes no judgments of its own.
"""
import argparse, json, subprocess, sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
RUNS = ROOT / "runs"
POS_VERDICTS = {"PRIMARY", "SUPPORT"}
NEG_VERDICTS = {"WEAK", "DROP", "BLOCKED"}


def score(urls_file: Path, mode: str, field: str) -> dict:
    """Run the real scorer. Its output is the thing under test, so shell out rather than import."""
    out = subprocess.run(
        [sys.executable, str(ROOT.parent / "scripts" / "srcscore.py"),
         "--in", str(urls_file), "--mode", mode, "--field", field, "--format", "json"],
        capture_output=True, text=True, check=True).stdout
    rows = json.loads(out)
    if isinstance(rows, dict):
        rows = rows.get("results") or rows.get("rows") or []
    return {r["url"]: r for r in rows}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("run_id")
    ap.add_argument("--mode", required=True)
    ap.add_argument("--field", required=True)
    args = ap.parse_args()

    judge_doc = json.loads((RUNS / f"{args.run_id}.judge.json").read_text())
    judged = judge_doc["judged"]
    scored = score(RUNS / f"{args.run_id}.urls.txt", args.mode, args.field)

    rows, unscored = [], []
    for j in judged:
        s = scored.get(j["url"])
        if not s:
            unscored.append(j["url"])
            continue
        v = s.get("verdict", "")
        rows.append({**j, "score": s.get("score"), "verdict": v, "tier": s.get("tier"),
                     "signals": s.get("signals"), "matched": s.get("matched"),
                     "policy_side": "pos" if v in POS_VERDICTS else "neg" if v in NEG_VERDICTS else "skim"})

    def sel(judgment, side, pool=None):
        pool = rows if pool is None else pool
        return [r for r in pool if r["judgment"] == judgment and r["policy_side"] == side]

    recall_loss = sel("useful", "neg")
    precision_loss = sel("junk", "pos")
    agree_pos, agree_neg = sel("useful", "pos"), sel("junk", "neg")

    vendors = [r for r in rows if r.get("vendor_interest")]
    vendor_passed = [r for r in vendors if r["policy_side"] == "pos"]

    report = {
        "id": args.run_id, "mode": args.mode, "field": args.field,
        "n_judged": len(judged), "n_scored": len(rows), "n_unscored": len(unscored),
        "judge_counts": {k: sum(1 for r in rows if r["judgment"] == k)
                         for k in ("useful", "marginal", "junk")},
        "basis_fetched": sum(1 for r in rows if r.get("basis") == "fetched"),
        "recall_loss": {"n": len(recall_loss),
                        "rate": round(len(recall_loss) / max(1, len(recall_loss) + len(agree_pos)), 3),
                        "cases": sorted(recall_loss, key=lambda r: r["score"] or 0)},
        "precision_loss": {"n": len(precision_loss),
                           "rate": round(len(precision_loss) / max(1, len(precision_loss) + len(agree_neg)), 3),
                           "cases": precision_loss},
        "skim_band": {"useful": len(sel("useful", "skim")), "junk": len(sel("junk", "skim"))},
        "vendor": {"n": len(vendors), "passed_filter": len(vendor_passed),
                   "mean_rank_search": round(sum(r["rank"] for r in vendors) / len(vendors), 1) if vendors else None,
                   "cases": vendor_passed},
        "unscored": unscored,
    }

    # Two-round (keyword-expansion) runs only. Absent `round` means a one-shot run and the
    # block is omitted entirely, so the five pilot files re-run to the same numbers.
    if any("round" in r for r in rows):
        by_round = {}
        for n in sorted({r.get("round", 1) for r in rows}):
            pool = [r for r in rows if r.get("round", 1) == n]
            loss, kept = sel("useful", "neg", pool), sel("useful", "pos", pool)
            by_round[str(n)] = {
                "n": len(pool),
                "useful": sum(1 for r in pool if r["judgment"] == "useful"),
                "recall_loss": {"n": len(loss),
                                "rate": round(len(loss) / max(1, len(loss) + len(kept)), 3)},
                # PILOT.md F1: tier 5 is the unregistered default, so the band and the
                # registration are counted apart -- `matched: null` is the scorer saying the
                # domain is not in the table at all.
                "tier5": sum(1 for r in pool if str(r["tier"]) == "5"),
                "unregistered": sum(1 for r in pool if not r["matched"]),
            }
        report["expansion"] = {"keywords": judge_doc.get("expansion_keywords", []),
                               "by_round": by_round}

    (RUNS / f"{args.run_id}.contrast.json").write_text(json.dumps(report, indent=2))

    c = report["judge_counts"]
    print(f"{args.run_id} [{args.mode}/{args.field}]  judged={report['n_scored']} "
          f"useful={c['useful']} marginal={c['marginal']} junk={c['junk']} "
          f"fetched={report['basis_fetched']}")
    print(f"  recall loss  {report['recall_loss']['n']:>3}  (rate {report['recall_loss']['rate']}) "
          f"<- useful sources the filter would drop")
    print(f"  precision loss {report['precision_loss']['n']:>1}  (rate {report['precision_loss']['rate']}) "
          f"<- junk the filter promoted")
    print(f"  vendor {report['vendor']['n']} judged, {report['vendor']['passed_filter']} passed filter")
    for n, b in report.get("expansion", {}).get("by_round", {}).items():
        print(f"  round {n}  n={b['n']:>3} useful={b['useful']:>3} "
              f"recall loss {b['recall_loss']['n']:>3} (rate {b['recall_loss']['rate']}) "
              f"tier5={b['tier5']} unregistered={b['unregistered']}")


if __name__ == "__main__":
    main()
