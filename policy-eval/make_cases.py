#!/usr/bin/env python3
"""Write one `claude plugin eval` case per question into policy-eval/arms/<id>/case.yaml.

The two arms (plugin loaded / no plugin) get the same prompt, so the prompt names scored web search only as
words; in the baseline arm nothing answers to them. The graders exist because a case must have one: the
regex keeps the trace, `tool_used: Skill` reports whether the skill fired in the with-arm.
"""
import argparse, json
from pathlib import Path

ROOT = Path(__file__).resolve().parent
PILOT_ORDINALS = (1, 2, 3, 6)
# Run beside the pilot set: od9 tests an unregistered official docs host (pending evals E1).
EXTRA_IDS = ("od9",)

CASE = """schema_version: "1.1"
name: {id}
runs: 1
execution:
  model: claude-opus-5-5
  max_turns: 80
  timeout_seconds: 1800
  allowed_tools: [WebSearch, WebFetch, Agent, Skill, Bash, Read, Write]
  prompt: |
    Research this question with scored web search, mode {mode}, field {field}, and write a short sourced report: {q}
graders:
  - name: keep-trace
    type: regex
    target: trace
    pattern: "[\\\\s\\\\S]*"
  - name: skill-fired
    type: tool_used
    tool: Skill
    arm: with-only
"""


def pilot_questions():
    doc = json.loads((ROOT / "questions.json").read_text())
    for mode, block in doc["modes"].items():
        for n, q in enumerate(block["questions"], 1):
            if n in PILOT_ORDINALS:
                yield {**q, "mode": mode, "expansion": bool(q.get("expansion"))}


def case_questions():
    """The pilot questions, then the extra ones; the arms run and `analyze` cover exactly these."""
    yield from pilot_questions()
    doc = json.loads((ROOT / "questions.json").read_text())
    for mode, block in doc["modes"].items():
        for q in block["questions"]:
            if q["id"] in EXTRA_IDS:
                yield {**q, "mode": mode, "expansion": bool(q.get("expansion"))}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--list", action="store_true", help="print the pilot questions as JSON instead of writing cases")
    args = ap.parse_args()
    qs = list(case_questions())
    if args.list:
        print(json.dumps([{k: q[k] for k in ("id", "mode", "field", "expansion", "q")} for q in qs]))
        return
    for q in qs:
        d = ROOT / "arms" / q["id"]
        d.mkdir(parents=True, exist_ok=True)
        (d / "case.yaml").write_text(CASE.format(**q))
    print(f"wrote {len(qs)} cases")


if __name__ == "__main__":
    main()
