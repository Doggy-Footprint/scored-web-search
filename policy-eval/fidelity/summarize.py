#!/usr/bin/env python3
"""Aggregate the per-topic audits into fidelity/summary.json.

`<id>/result.json` holds the Haiku-caller round (WebSearch and WebFetch); `<id>/result.opus.json` the
Opus-caller WebSearch round. WebFetch has no Opus round: its summariser is Haiku whatever model calls it.
"""
import json
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent
VERDICTS = ("faithful", "minor loss", "material loss", "not assessable")


def tally(records):
    verdicts, counts = Counter(), Counter()
    for r in records:
        verdicts[r["verdict"]] += 1
        counts.update(r["counts"])
    return {"n": len(records), "verdicts": {v: verdicts[v] for v in VERDICTS}, "counts": dict(sorted(counts.items()))}


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
        "per_topic": per_topic,
    }
    (ROOT / "summary.json").write_text(json.dumps(summary, indent=1, ensure_ascii=False) + "\n")
    for k in ("websearch_haiku", "websearch_opus", "webfetch_haiku"):
        print(k, summary[k]["verdicts"], summary[k]["counts"])


if __name__ == "__main__":
    main()
