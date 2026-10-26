#!/usr/bin/env python3
"""With-plugin vs. no-plugin arms: pull raw data out of `claude plugin eval` traces, then measure them.

    arms.py ingest <result.json>   copy each run's trace into runs/, extract events, list URLs the blind
                                   judge has not labelled (runs/<id>.union.todo.json)
    arms.py analyze                join events with blind labels and scores -> runs/<id>.arms.json and
                                   runs/pilot.summary.json

Exposure levels, per URL and per arm, counted at the highest one reached (METHODOLOGY.md):
    listed   URL/title in the main agent's context (search-result links without their summary)
    exposed  search-result text in the main context (the WebSearch tool's summary of its own results)
    read     page content fetched by the main agent
    sub_read page content fetched by a subagent -- never in the main context
"""
import argparse, hashlib, json, re, shutil, subprocess, sys
from collections import Counter, defaultdict
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit, parse_qsl, urlencode

import pricing

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "skills" / "scored-web-search" / "scripts"))
from srcscore_core.identifiers import fetch_url  # noqa: E402

ROOT = Path(__file__).resolve().parent
RUNS = ROOT / "runs"
REPO = ROOT.parent
SCORER = REPO / "skills" / "scored-web-search" / "scripts" / "srcscore.py"
LEVELS = ("listed", "exposed", "read")
LABELS = ("useful", "marginal", "junk", "unlabeled")
URL_RE = re.compile(r"https?://[^\s\"'<>\]\)|`]+")


def norm(url):
    """Join key for URLs from different tools: lowercase host, no fragment/trailing slash/tracking params.

    The scorer's fetch address (fetch_url) is applied first, so a rewritten PMC/arXiv URL in a score table or
    WebFetch call joins the label of the URL the search returned.
    """
    try:
        p = urlsplit(fetch_url(url.strip().rstrip(".,;")))
    except ValueError:
        return url
    q = urlencode([(k, v) for k, v in parse_qsl(p.query) if not k.lower().startswith("utm_")])
    host = p.netloc.lower().removeprefix("www.")
    return urlunsplit((p.scheme.lower(), host, p.path.rstrip("/") or "/", q, ""))


def text_of(content):
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "\n".join(b.get("text", "") for b in content if isinstance(b, dict))
    return ""


def search_links(text):
    """The `Links: [...]` JSON array of a WebSearch result, plus whatever text follows it."""
    i = text.find("Links:")
    if i < 0:
        return [], ""
    try:
        links, end = json.JSONDecoder().raw_decode(text, i + len("Links:") + (1 if text[i + 6:i + 7] == " " else 0))
    except ValueError:
        return [{"url": u} for u in URL_RE.findall(text)], ""
    return [l for l in links if isinstance(l, dict) and l.get("url")], text[end:].strip()



# ---------------------------------------------------------------- ingest

def extract(trace_path):
    """One pass over a stream-json trace. Roles: 'main' or the subagent type that owns the message."""
    with open(trace_path) as fh:
        lines = [json.loads(l) for l in fh if l.strip()]
    role_of = {}                      # Agent tool_use id -> subagent type
    tool_use = {}                     # tool_use id -> (role, name, input)
    agent_type = {}                   # subagent agentId -> type, from the Agent tool results
    ev = {"searches": [], "fetches": [], "agents": [], "skill_fired": False, "report_chars": 0, "result": None}

    for d in lines:
        if d.get("type") == "system" and d.get("subtype") == "task_started":
            role_of[d.get("tool_use_id")] = d.get("subagent_type") or "subagent"
            ev["agents"].append({"tool_use_id": d.get("tool_use_id"), "type": d.get("subagent_type"),
                                 "description": d.get("description")})
        if d.get("type") == "result":
            ev["result"] = {k: d.get(k) for k in ("total_cost_usd", "modelUsage", "usage", "num_turns",
                                                  "duration_ms", "subagent_stats", "is_error", "subtype")}
            ev["report_chars"] = len(d.get("result") or "")
            ev["report"] = d.get("result") or ""

    for d in lines:
        role = role_of.get(d.get("parent_tool_use_id"), "main") if d.get("parent_tool_use_id") else "main"
        if d.get("type") == "assistant":
            m = d["message"]
            for b in m.get("content", []):
                if b.get("type") == "tool_use":
                    tool_use[b["id"]] = (role, b["name"], b.get("input") or {})
                    if b["name"] == "Skill":
                        ev["skill_fired"] = True
        elif d.get("type") == "user" and isinstance(d.get("message", {}).get("content"), list):
            tur = d.get("tool_use_result")
            if isinstance(tur, dict) and tur.get("agentId"):
                agent_type[tur["agentId"]] = tur.get("agentType")
            for b in d["message"]["content"]:
                if b.get("type") != "tool_result" or b.get("tool_use_id") not in tool_use:
                    continue
                trole, name, inp = tool_use[b["tool_use_id"]]
                text = text_of(b.get("content"))
                if name == "WebSearch":
                    links, rest = search_links(text)
                    ev["searches"].append({"role": trole, "query": inp.get("query"),
                                           "links": [{"url": l["url"], "title": l.get("title")} for l in links],
                                           "summary_chars": len(rest)})
                elif name == "WebFetch":
                    code = tur.get("code") if isinstance(tur, dict) else None
                    ok = not b.get("is_error") and (code is None or 200 <= int(code) < 400) and bool(text.strip())
                    ev["fetches"].append({"role": trole, "url": inp.get("url"), "ok": ok, "code": code,
                                          "bytes": tur.get("bytes") if isinstance(tur, dict) else None,
                                          "result_chars": len(text),
                                          "error": None if ok else text[:200]})

    session = next((d.get("session_id") for d in lines if d.get("subtype") == "init"), None)
    ev["usage_by_role"], ev["main_peak_context"] = session_usage(Path(trace_path), session, agent_type)
    return ev


def session_files(trace_path, session):
    """The run's own session transcripts. The stream trace carries only interim usage per message; these
    carry the final one, and their sum matches the result's modelUsage for the main model."""
    base = trace_path.parent.parent / "config" / "projects"
    hits = list(base.glob(f"*/{session}.jsonl")) if session else []
    if not hits:
        return None, []
    main = hits[0]
    return main, sorted((main.parent / session / "subagents").glob("agent-*.jsonl"))


def session_usage(trace_path, session, agent_type):
    main, subs = session_files(trace_path, session)
    rows, peak = [], 0
    for f in ([main] if main else []) + subs:
        role = "main" if f == main else agent_type.get(f.stem.removeprefix("agent-"), "subagent")
        last = {}
        with open(f) as fh:
            for l in fh:
                d = json.loads(l)
                if d.get("type") == "assistant" and d.get("message", {}).get("id"):
                    last[d["message"]["id"]] = (d["message"].get("model"), d["message"].get("usage") or {})
        acc = defaultdict(lambda: defaultdict(float))
        for model, u in last.values():
            a = acc[model]
            for k in ("input_tokens", "output_tokens", "cache_read_input_tokens", "cache_creation_input_tokens"):
                a[k] += u.get(k, 0) or 0
            try:
                a["usd"] += pricing.usage_usd(model, u)
            except KeyError:
                a["unpriced"] += 1
            a["requests"] += 1
            if role == "main":
                peak = max(peak, sum(u.get(k, 0) or 0 for k in ("input_tokens", "cache_read_input_tokens",
                                                                 "cache_creation_input_tokens")))
        rows += [{"role": role, "agent_file": f.name, "model": m, **a} for m, a in acc.items()]
    return rows, peak



def cmd_ingest(result_path):
    res = json.loads(Path(result_path).read_text())
    for case in res.get("cases", []):
        cid = case["name"]
        pool = set()
        for arm in ("with", "without"):
            for i, run in enumerate(case.get("arms", {}).get(arm, [])):
                tp = run.get("tracePath")
                if not tp or not Path(tp).exists():
                    print(f"{cid} {arm}: no trace ({run.get('error')})", file=sys.stderr)
                    continue
                shutil.copyfile(tp, RUNS / f"{cid}.{arm}.trace.jsonl")
                ev = extract(tp)
                with open(tp) as fh:
                    sess = next((json.loads(l).get("session_id") for l in fh if '"init"' in l), None)
                main, subs = session_files(Path(tp), sess)
                raw = RUNS / f"{cid}.{arm}.session"
                if main:
                    raw.mkdir(exist_ok=True)
                    for f in [main] + subs:
                        shutil.copyfile(f, raw / f.name)
                ev.update({"id": cid, "arm": arm, "eval_cost_usd": run.get("costUsd"), "turns": run.get("turns"),
                           "duration_s": run.get("durationSeconds"), "error": run.get("error"),
                           "started_at": run.get("startedAt")})
                (RUNS / f"{cid}.{arm}.report.md").write_text(ev.pop("report", ""))
                (RUNS / f"{cid}.{arm}.events.json").write_text(json.dumps(ev, indent=1))
                pool |= set(arm_urls(ev))
                if i:
                    print(f"{cid} {arm}: only the first run is analysed", file=sys.stderr)
        jp = RUNS / f"{cid}.judge.json"
        labelled = {norm(j["url"]) for j in json.loads(jp.read_text())["judged"]} if jp.exists() else set()
        titles = {}
        for arm in ("with", "without"):
            p = RUNS / f"{cid}.{arm}.events.json"
            if p.exists():
                for u, t in arm_titles(json.loads(p.read_text())).items():
                    titles.setdefault(u, t)
        todo = sorted(pool - labelled, key=lambda u: hashlib.sha1(u.encode()).hexdigest())
        (RUNS / f"{cid}.union.todo.json").write_text(json.dumps([{"url": u, "title": titles.get(u)} for u in todo], indent=1))
        print(f"{cid}: {len(pool)} arm URLs, {len(todo)} need labels")


# ---------------------------------------------------------------- analyze

def arm_urls(ev):
    """norm(url) -> the highest level it reached in this arm. Main-context levels outrank subagent ones."""
    order = {l: i for i, l in enumerate(("sub_seen", "sub_read") + LEVELS)}
    lvl = {}

    def bump(u, level):
        u = norm(u)
        if order[level] > order.get(lvl.get(u), -1):
            lvl[u] = level

    for s in ev["searches"]:
        for l in s["links"]:
            if s["role"] == "main":
                bump(l["url"], "exposed" if s["summary_chars"] else "listed")
            else:
                bump(l["url"], "sub_seen")
    for f in ev["fetches"]:
        if f["url"] and f["ok"]:
            bump(f["url"], "read" if f["role"] == "main" else "sub_read")
    return lvl


def arm_titles(ev):
    t = {}
    for s in ev["searches"]:
        for l in s["links"]:
            t.setdefault(norm(l["url"]), l.get("title"))
    return t


def score_all(urls, mode, field):
    """Score every URL once with the real scorer (dates where the blind judge recorded them)."""
    if not urls:
        return {}
    tmp = RUNS / "_score_in.json"
    tmp.write_text(json.dumps([{"url": u, **({"date": d} if d else {})} for u, d in urls.items()]))
    argv = [sys.executable, str(SCORER), "--in", str(tmp), "--mode", mode, "--field", field, "--format", "json"]
    r = subprocess.run(argv, capture_output=True, text=True)
    if r.returncode == 4:
        # Citation/star lookups unreachable (rate limits while eval runs share the network). Tiers come from the
        # domain table alone, so they are unaffected; scores and verdicts lose the network signals.
        r = subprocess.run(argv + ["--no-net"], capture_output=True, text=True)
    r.check_returncode()
    tmp.unlink()
    return {norm(row["url"]): row for row in json.loads(r.stdout)}


def cost_breakdown(ev):
    res = ev.get("result") or {}
    mu = res.get("modelUsage") or {}
    by_model = {m: {k: v.get(k) for k in ("inputTokens", "outputTokens", "cacheReadInputTokens",
                                          "cacheCreationInputTokens", "webSearchRequests", "costUSD")}
                for m, v in mu.items()}
    total = sum((v.get("costUSD") or 0) for v in mu.values())
    by_role = defaultdict(float)
    for r in ev["usage_by_role"]:
        by_role["main" if r["role"] == "main" else "subagent"] += r["usd"]
    searches = sum((v.get("webSearchRequests") or 0) for v in mu.values())
    traced_by_model = defaultdict(float)
    for r in ev["usage_by_role"]:
        traced_by_model[pricing.canonical(r["model"]) or r["model"]] += r["usd"]
    # modelUsage covers every API call of the run; what no transcript attributes is the tools' own model
    # calls (WebSearch/WebFetch run a request on the caller's model).
    tool_internal = {m: round((v.get("costUSD") or 0) - traced_by_model.get(pricing.canonical(m) or m, 0)
                              - (v.get("webSearchRequests") or 0) * pricing.WEB_SEARCH_USD, 4)
                     for m, v in mu.items()}
    return {"total_usd": round(total, 4), "eval_cost_usd": ev.get("eval_cost_usd"), "by_model": by_model,
            "by_role_usd": {k: round(v, 4) for k, v in by_role.items()},
            "web_search_requests": searches, "web_search_usd": round(searches * pricing.WEB_SEARCH_USD, 4),
            "tool_internal_usd": tool_internal}


def analyze_arm(ev, labels, scores, q):
    lvl = arm_urls(ev)
    contamination = {L: {lab: Counter() for lab in LABELS} for L in LEVELS + ("sub_read",)}
    for u, L in lvl.items():
        if L not in contamination:
            continue
        lab = labels.get(u, {}).get("judgment", "unlabeled")
        tier = str(scores.get(u, {}).get("tier", "?"))
        contamination[L][lab][tier] += 1
    fetch_main = [f for f in ev["fetches"] if f["role"] == "main"]
    fetch_sub = [f for f in ev["fetches"] if f["role"] != "main"]
    read_urls = {norm(f["url"]) for f in fetch_main if f["ok"]}
    return {
        "skill_fired": ev["skill_fired"], "error": ev.get("error"), "turns": ev.get("turns"),
        "duration_s": ev.get("duration_s"),
        "contamination": {L: {lab: dict(c) for lab, c in d.items()} for L, d in contamination.items()},
        "sources_read": {"main_attempted": len(fetch_main), "main_ok": len(read_urls),
                         "main_by_label": dict(Counter(labels.get(u, {}).get("judgment", "unlabeled") for u in read_urls)),
                         "sub_attempted": len(fetch_sub), "sub_ok": sum(f["ok"] for f in fetch_sub)},
        "fetch_failures": [{"url": f["url"], "code": f["code"], "error": f["error"]} for f in ev["fetches"] if not f["ok"]],
        "cost": cost_breakdown(ev),
        "searches_main": sum(1 for s in ev["searches"] if s["role"] == "main"),
        "searches_sub": sum(1 for s in ev["searches"] if s["role"] != "main"),
        "main_peak_context_tokens": ev["main_peak_context"], "report_chars": ev["report_chars"],
        "read_urls": sorted(read_urls), "useful_reached": sorted(u for u, L in lvl.items()
                                                                if labels.get(u, {}).get("judgment") == "useful"),
    }


def cmd_analyze():
    qs = {q["id"]: q for q in json.loads(subprocess.run([sys.executable, str(ROOT / "make_cases.py"), "--list"],
                                                       capture_output=True, text=True, check=True).stdout)}
    summary = {}
    for cid, q in qs.items():
        evs = {a: json.loads((RUNS / f"{cid}.{a}.events.json").read_text())
               for a in ("with", "without") if (RUNS / f"{cid}.{a}.events.json").exists()}
        if not evs:
            continue
        labels = {}
        for name in (f"{cid}.judge.json", f"{cid}.union.judge.json"):
            p = RUNS / name
            if p.exists():
                for j in json.loads(p.read_text())["judged"]:
                    labels.setdefault(norm(j["url"]), j)
        dates = {}
        up = RUNS / f"{cid}.urls.json"
        if up.exists():
            dates = {norm(r["url"]): r.get("date") for r in json.loads(up.read_text())}
        urls = dict(dates)
        for ev in evs.values():
            for u in arm_urls(ev):
                urls.setdefault(u, None)
        scores = score_all(urls, q["mode"], q["field"])
        net_off = any("net:off" in (r.get("flags") or []) for r in scores.values())
        out = {"id": cid, "mode": q["mode"], "field": q["field"], "expansion": q["expansion"],
               "n_labels": len(labels), "scores_net_off": net_off, "arms": {a: analyze_arm(ev, labels, scores, q) for a, ev in evs.items()}}
        if len(evs) == 2:
            w, o = (set(out["arms"][a]["read_urls"]) for a in ("with", "without"))
            uw, uo = (set(out["arms"][a]["useful_reached"]) for a in ("with", "without"))
            out["overlap"] = {"read_jaccard": round(len(w & o) / max(1, len(w | o)), 3),
                              "useful_only_without": sorted(uo - uw), "useful_only_with": sorted(uw - uo)}
        (RUNS / f"{cid}.arms.json").write_text(json.dumps(out, indent=1))
        summary[cid] = out
        print(f"{cid}: " + "  ".join(f"{a}: read {r['sources_read']['main_ok']} ${r['cost']['total_usd']}"
                                      for a, r in out["arms"].items()))
    (RUNS / "pilot.summary.json").write_text(json.dumps(summary, indent=1))


def main():
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("ingest").add_argument("result")
    sub.add_parser("analyze")
    a = ap.parse_args()
    RUNS.mkdir(exist_ok=True)
    cmd_ingest(a.result) if a.cmd == "ingest" else cmd_analyze()


if __name__ == "__main__":
    main()
