#!/usr/bin/env python3
"""Aggregate the per-topic audits into fidelity/summary.json.

`<id>/result.json` holds the Haiku-caller round (WebSearch and WebFetch); `<id>/result.opus.json` the
Opus-caller WebSearch round. WebFetch has no Opus round: its summariser is Haiku whatever model calls it.
`<id>/result.ab.json` is the WebFetch prompt A/B (see METHODOLOGY.md, "Summary fidelity"): baseline prompt B vs
B + the SKILL.md fidelity block T, two runs each, judged blind.
"""
import json
from collections import Counter
from math import comb
from pathlib import Path

ROOT = Path(__file__).resolve().parent
VERDICTS = ("faithful", "minor loss", "material loss", "not assessable")


def tally(records):
    verdicts, counts = Counter(), Counter()
    for r in records:
        verdicts[r["verdict"]] += 1
        counts.update(r["counts"])
    return {"n": len(records), "verdicts": {v: verdicts[v] for v in VERDICTS}, "counts": dict(sorted(counts.items()))}


LOSS = {"faithful": 0, "minor loss": 1, "material loss": 2}


def sign_test(diffs):
    """Two-sided exact sign test p-value over the non-zero paired differences."""
    nz = [d for d in diffs if d]
    n, k = len(nz), sum(d > 0 for d in nz)
    if not n:
        return {"pos": 0, "neg": 0, "ties": len(diffs), "p": 1.0}
    tail = sum(comb(n, i) for i in range(0, min(k, n - k) + 1)) / 2 ** n
    return {"pos": k, "neg": n - k, "ties": len(diffs) - n, "p": round(min(1.0, 2 * tail), 4)}


def ab_summary(results):
    arms = {"B": [], "T": []}
    paired, noise = [], {"B": [], "T": []}
    for res in results:
        by = {(r["arm"], r["run"]): r for r in res["summaries"]}
        loss = {k: LOSS.get(r["verdict"], 0) for k, r in by.items()}
        for (arm, _), r in by.items():
            arms[arm].append(r)
        # Positive = T loses less than B on this topic, summed over both runs.
        paired.append(loss[("B", 1)] + loss[("B", 2)] - loss[("T", 1)] - loss[("T", 2)])
        for arm in arms:
            noise[arm].append(loss[(arm, 1)] - loss[(arm, 2)])
    out = {}
    for arm, rs in arms.items():
        t = tally(rs)
        t["false_absence"] = sum(r.get("false_absence", 0) for r in rs)
        t["mean_chars"] = round(sum(r["chars"] for r in rs) / max(1, len(rs)))
        out[arm] = t
    out["paired_T_vs_B"] = sign_test(paired)
    out["noise_run1_vs_run2"] = {arm: sign_test(v) for arm, v in noise.items()}
    out["n_topics"] = len(results)
    return out


def main():
    haiku_ws, haiku_wf, opus_ws, per_topic = [], [], [], []
    for d in sorted(p for p in ROOT.iterdir() if p.is_dir() and (p / "result.json").exists()):
        h = json.loads((d / "result.json").read_text())
        haiku_ws.append(h["websearch"])
        haiku_wf.append(h["webfetch"])
        row = {"id": h["id"], "kind": h["kind"], "websearch_haiku": h["websearch"]["verdict"],
               "webfetch_haiku": h["webfetch"]["verdict"]}
        if (d / "result.opus.json").exists():
            o = json.loads((d / "result.opus.json").read_text())
            opus_ws.append(o)
            row.update(websearch_opus=o["verdict"], haiku_worst_repeated=o["haiku_worst_repeated"],
                       links_shared=f'{o["links_shared_with_haiku_round"]}/{o["links_total"]}')
        per_topic.append(row)
    summary = {
        "websearch_haiku": tally(haiku_ws),
        "websearch_opus": tally(opus_ws),
        "webfetch_haiku": tally(haiku_wf),
        "haiku_worst_repeated_in_opus": dict(Counter(o["haiku_worst_repeated"] for o in opus_ws)),
        "webfetch_ab": ab_summary([json.loads(p.read_text()) for p in sorted(ROOT.glob("*/result.ab.json"))]),
        "per_topic": per_topic,
    }
    (ROOT / "summary.json").write_text(json.dumps(summary, indent=1, ensure_ascii=False) + "\n")
    for k in ("websearch_haiku", "websearch_opus", "webfetch_haiku"):
        print(k, summary[k]["verdicts"], summary[k]["counts"])
    print("webfetch_ab", json.dumps(summary["webfetch_ab"]))


if __name__ == "__main__":
    main()
