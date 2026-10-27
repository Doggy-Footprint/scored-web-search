"""Input parsing and output rendering (table / markdown / URL list)."""

from __future__ import annotations

import json
import re

__all__ = ["parse_input", "tier_label", "render_table", "render_compact", "render_md", "VERDICTS"]

VERDICTS = ("PRIMARY", "SUPPORT", "SKIM", "WEAK", "DROP", "BLOCKED")


def parse_input(text: str) -> list:
    text = text.strip()
    if not text:
        return []
    if text[0] in "[{":
        try:
            j = json.loads(text)
            if isinstance(j, dict):
                j = j.get("results") or j.get("urls") or []
            out = []
            for e in j:
                if isinstance(e, str):
                    out.append({"url": e, "title": ""})
                elif isinstance(e, dict) and e.get("url"):
                    out.append({"url": e["url"], "title": e.get("title", ""),
                                **({"date": e["date"]} if "date" in e else {})})
            if out:
                return out
        except ValueError:
            pass
    out, seen = [], set()
    for line in text.splitlines():
        line = line.strip().lstrip("-*• ").strip()
        if not line:
            continue
        title = ""
        if "|" in line:
            a, b = line.split("|", 1)
            if a.strip().startswith("http"):
                line, title = a.strip(), b.strip()
            else:
                line, title = b.strip(), a.strip()
        m = re.search(r"https?://\S+", line)
        if not m:
            continue
        u = m.group(0).rstrip(").,;\"'")
        if u in seen:
            continue
        seen.add(u)
        out.append({"url": u, "title": title})
    return out


def tier_label(tier: str) -> str:
    """Compact column value: the blocklist tier prints as 0."""
    return "0" if tier == "block" else str(tier)


def _url(r: dict) -> str:
    """Rendered output names the address to fetch; JSON keeps the input `url` as the row's identity."""
    return r.get("fetch_url") or r["url"]


def _row_line(r: dict) -> str:
    sig = ",".join(r["signals"] + r["flags"])[:27]
    return "%5.1f %-8s %-2s %-27s %s" % (r["score"], r["verdict"], tier_label(r["tier"]), sig, _url(r))


def render_table(rows: list) -> str:
    if not rows:
        return "(no sources)"
    lines = ["SCORE VERDICT T  SIGNALS                     URL"]
    lines += [_row_line(r) for r in rows]
    citable = sum(1 for r in rows if r["verdict"] in ("PRIMARY", "SUPPORT"))
    dropped = sum(1 for r in rows if r["verdict"] in ("DROP", "BLOCKED"))
    lines.append("-- %d sources | %d citable | %d dropped" % (len(rows), citable, dropped))
    return "\n".join(lines)


COMPACT_TITLE_CHARS = 40


def render_compact(rows: list) -> str:
    """Only the rows Step 3 may act on, each with a cut title to choose by; the rest of the table is counts.
    Without the title the main agent went looking for it in the URL files and the full table."""
    if not rows:
        return "(no sources)"
    lines = ["SCORE VERDICT T  SIGNALS                     URL  TITLE"]
    for r in rows:
        if r["verdict"] in ("PRIMARY", "SUPPORT"):
            title = " ".join((r.get("title") or "").split())[:COMPACT_TITLE_CHARS]
            lines.append(_row_line(r) + ("  " + title if title else ""))
    counts = " | ".join("%s %d" % (v, sum(1 for r in rows if r["verdict"] == v)) for v in VERDICTS)
    lines.append("-- %d sources | %s" % (len(rows), counts))
    return "\n".join(lines)


def render_md(rows: list) -> str:
    out = ["| Score | Verdict | T | Signals | Source |", "|---|---|---|---|---|"]
    for r in rows:
        t = (r["title"] or _url(r))[:70].replace("|", "/")
        sig = ", ".join(r["signals"] + r["flags"]) or "-"
        out.append("| %.1f | %s | %s | %s | [%s](%s) |"
                   % (r["score"], r["verdict"], tier_label(r["tier"]), sig, t, _url(r)))
    return "\n".join(out)
