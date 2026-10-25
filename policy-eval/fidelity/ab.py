#!/usr/bin/env python3
"""WebFetch prompt A/B: baseline prompt (B) vs the same prompt + SKILL.md Step 3 fidelity block (T).

  ab.py question <id>           print the baseline request (the judge's QUESTION)
  ab.py prompt <id>             print the fetcher agent's prompt (4 WebFetch calls: B, T, B, T)
  ab.py collect <id> <agentId>  extract the 4 results, refetch the page, write blinded S1..S4
  ab.py merge                   join each <id>/judge.ab.json with ab.map.json into result.ab.json

BLOCK is the fidelity block that SKILL.md Step 3 carried during the A/B. It was taken out of the skill afterwards
(no measurable gain, summaries 25% longer; see summary.json `webfetch_ab`), so the tested text lives here.
"""
import hashlib, json, re, subprocess, sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
BLOCK = 'Fidelity rules: (1) Quote key figures, results and conclusions verbatim, in short quotes. (2) Give dates, versions and status (for example fixed, closed, superseded, interim, topline) exactly as the page states them. (3) Keep qualifiers: population, conditions, scope, and hedges such as "may" or "in mice". (4) If something asked for is not in the text you were given, write "not found in the text read", never "not reported" or "does not exist"; if the text was truncated, say so. (5) Mark anything that is your own inference with "inference:" and add no context that is not on the page.'
ORDER = ("B", "T", "B", "T")


def block():
    return BLOCK


def base(tid):
    w = json.loads((ROOT / tid / "result.json").read_text())["webfetch"]
    if "fetch_prompt" in w:
        return w["url"], w["fetch_prompt"]
    # Older records (d01) kept the prompt only in the verbatim tool input.
    head = (ROOT / tid / "webfetch.txt").read_text().split("\n", 1)[0]
    return w["url"], json.loads(head[len("INPUT: "):])["prompt"]


def prompts(tid):
    url, p = base(tid)
    return url, {"B": p, "T": p + "\n\n" + block()}


def cmd_prompt(tid):
    url, ps = prompts(tid)
    calls = "\n".join(f"{i + 1}. prompt: {json.dumps(ps[c])}" for i, c in enumerate(ORDER))
    print(f"Call the WebFetch tool 4 times, one call at a time and in this order, each on the URL {url} "
          f"with the prompt given below. Call no other tool. Then reply with only DONE.\n{calls}")


def cmd_question(tid):
    print(base(tid)[1])


def cmd_collect(tid, aid):
    d = ROOT / tid
    url, ps = prompts(tid)
    by_cond = {"B": [], "T": []}
    for n in range(4):
        out = d / f"ab.raw{n + 1}.txt"
        subprocess.run([sys.executable, str(ROOT / "toolresult.py"), aid, "WebFetch", str(out), str(n)], check=True)
        head, _, body = out.read_text().partition("\n\n=== RESULT ===\n")
        prompt = json.loads(head[len("INPUT: "):]).get("prompt", "")
        # The agent may re-escape the newlines, so classify by content rather than byte equality.
        if not prompt.startswith(ps["B"][:80]):
            sys.exit(f"{tid}: call {n} prompt is not the baseline prompt")
        cond = "T" if block()[:60] in prompt else "B"
        by_cond[cond].append(body)
    if [len(v) for v in by_cond.values()] != [2, 2]:
        sys.exit(f"{tid}: expected 2 calls per arm, got {[len(v) for v in by_cond.values()]}")
    subprocess.run([sys.executable, str(ROOT / "pagetext.py"), url, str(d / "fetch.raw.ab.txt")], check=True)
    items = [(c, r, by_cond[c][r]) for c in ("B", "T") for r in range(2)]
    # Deterministic per-topic shuffle, so the judge cannot infer the arm from the slot.
    items.sort(key=lambda x: hashlib.sha256(f"{tid}:{x[0]}:{x[1]}".encode()).hexdigest())
    mapping = {}
    for i, (c, r, body) in enumerate(items, 1):
        (d / f"ab.S{i}.txt").write_text(body)
        mapping[f"S{i}"] = {"arm": c, "run": r + 1, "chars": len(body)}
    (d / "ab.map.json").write_text(json.dumps(mapping, indent=1) + "\n")
    print(tid, "ok", {k: v["chars"] for k, v in mapping.items()})


def cmd_merge():
    for d in sorted(p for p in ROOT.iterdir() if (p / "judge.ab.json").exists()):
        j = json.loads((d / "judge.ab.json").read_text())
        m = json.loads((d / "ab.map.json").read_text())
        rows = [dict(slot=s, **m[s], **j["summaries"][s]) for s in sorted(m)]
        (d / "result.ab.json").write_text(json.dumps({"id": d.name, "summaries": rows}, indent=1, ensure_ascii=False) + "\n")
        print(d.name, [(r["arm"], r["run"], r["verdict"]) for r in rows])


if __name__ == "__main__":
    cmd, *rest = sys.argv[1:]
    {"question": cmd_question, "prompt": cmd_prompt, "collect": cmd_collect, "merge": cmd_merge}[cmd](*rest)
