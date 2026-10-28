"""Independent spec tests: the Step 2.5 judge rubric, how SKILL.md hands it out, and the report's closing lines.

The rubric is the judge subagent's only instructions, so it must stand alone. SKILL.md Step 2.5 points at it
instead of carrying a second copy. Step 6 closes every report with the summary, `Degraded:` and `Keywords:`
lines; every Degraded item named elsewhere in SKILL.md must be defined there.
"""

import os
import re
import unittest

import _pathsetup  # noqa: F401

ROOT = _pathsetup.ROOT
SKILL = os.path.join(ROOT, "skills", "scored-web-search")
SKILL_PATH = os.path.join(SKILL, "SKILL.md")
RUBRIC_PATH = os.path.join(SKILL, "references", "judge-rubric.md")
PENDING_PATH = os.path.join(ROOT, "handoff", "be76cb7418ff8904-pending-evals.md")

DEGRADED_ITEMS = ("step1-inline", "lookups-failed", "judge-unavailable", "judge-skipped", "unjudged", "verify-skipped")


def read(p):
    with open(p, encoding="utf-8") as f:
        return f.read()


def norm(text):
    return re.sub(r"\s+", " ", text).lower()


def section(skill_text, step):
    """From the `### Step <step>` heading to the next `##`/`###` heading."""
    m = re.search(r"(?ms)^### *Step %s\b.*?(?=^#{2,3} )" % re.escape(step), skill_text + "\n## END\n")
    assert m, "Step %s section not found in SKILL.md" % step
    return m.group(0)


def sentences_with(text, *needles):
    parts = re.split(r"(?<=[.!?])\s+|\n\s*[-*] ", text)
    return [s for s in parts if all(n in s.lower() for n in needles)]


class JudgeRubricSelfContained(unittest.TestCase):
    def setUp(self):
        self.raw = read(RUBRIC_PATH)
        self.p = norm(self.raw)

    def test_states_inputs_tool_and_per_url_decision(self):
        self.assertIn("research question", self.p)
        self.assertIn("support", self.p)
        self.assertRegex(self.p, r"\b(each|every) (url|page)\b|\bper url\b")
        self.assertIn("webfetch", self.p)
        self.assertRegex(self.p, r"\bdecide\b|\bjudge\b")
        self.assertRegex(self.p, r"pages?\b|urls?\b")  # several of them, not one

    def test_no_dependency_on_outside_context(self):
        # The judge sees only this file: it must not lean on SKILL.md, step numbers or other references.
        for word in ("skill.md", "step 2.5", "step 3", "references/", "srcscore"):
            self.assertNotIn(word, self.p, word)

    def test_use_requires_all_three_criteria_else_skip(self):
        self.assertRegex(self.p, r"\buse\b[^.]*\ball\b[^.]*\bhold|\buse\b only when all")
        items = re.findall(r"(?m)^\s*\d+\.\s+(.+)$", self.raw)
        self.assertEqual(len(items), 3, items)
        a, b, c = (norm(i) for i in items)
        self.assertRegex(a, r"substantive|address")
        self.assertIn("question", a)
        self.assertRegex(b, r"evidence|data")
        self.assertRegex(b, r"attribut|named source")
        self.assertIn("marketing", c)
        self.assertIn("seo", c)
        self.assertRegex(c, r"thin|rewrite")
        self.assertRegex(c, r"\bnot\b")
        self.assertRegex(self.p, r"otherwise[^.]*\bskip\b")

    def test_unjudged_two_reason_kinds(self):
        self.assertRegex(self.p, r"unjudged[^.]*no page to judge")
        self.assertRegex(self.p, r"start[^.]*reason|reason[^.]*start")
        page = [l for l in self.raw.splitlines() if "`page:`" in l]
        fetch = [l for l in self.raw.splitlines() if "`fetch-error:`" in l]
        self.assertEqual(len(page), 1, page)
        self.assertEqual(len(fetch), 1, fetch)
        page, fetch = norm(page[0]), norm(fetch[0])
        self.assertRegex(page, r"http|status|404")
        self.assertIn("pdf", page)
        self.assertRegex(page, r"binary")
        self.assertIn("empty", page)
        self.assertIn("webfetch", fetch)
        self.assertRegex(fetch, r"fail")
        for cue in ("rate", "block", "time", "network"):
            self.assertIn(cue, fetch, cue)

    def test_fetch_error_retried_once_after_others_page_not(self):
        fetch = norm(next(l for l in self.raw.splitlines() if "`fetch-error:`" in l))
        self.assertRegex(fetch, r"once more|one more|again|retry|second")
        self.assertRegex(fetch, r"after (all )?the others|after (all|every) other|at the end|last")
        self.assertRegex(fetch, r"only (when|if) the second[^.]*fail|second (try|attempt)[^.]*fail")
        page = norm(next(l for l in self.raw.splitlines() if "`page:`" in l))
        self.assertNotRegex(page, r"once more|again|retry|second")

    def test_output_format_one_line_per_url(self):
        self.assertIn("url | use/skip/unjudged | reason", self.p)
        self.assertRegex(self.p, r"one line per url")
        self.assertRegex(self.p, r"\bonly\b[^.]*one line per url")
        self.assertRegex(self.p, r"input order")
        self.assertRegex(self.p, r"15 words")
        self.assertRegex(self.p, r"no page text")
        self.assertIn("quote", self.p)
        self.assertIn("summar", self.p)

    def test_old_single_page_format_gone(self):
        self.assertNotIn("reply with exactly one line", self.p)
        self.assertNotRegex(self.raw, r"\b(USE|SKIP|UNJUDGED)\|")
        self.assertNotRegex(self.raw, r"<reason>")


class SkillStep25(unittest.TestCase):
    def setUp(self):
        self.raw = section(read(SKILL_PATH), "2.5")
        self.p = norm(self.raw)

    def test_hands_every_support_url_and_question_to_lightweight_subagent(self):
        self.assertRegex(self.p, r"(every|all) support url")
        self.assertIn("research question", self.p)
        self.assertIn("lightweight subagent", self.p)
        self.assertRegex(self.p, r"no `?fork`?|not use `?fork")
        self.assertIn("references/judge-rubric.md", self.p)
        self.assertIn("verbatim", self.p)
        self.assertRegex(self.p, r"only instructions")

    def test_return_format_and_no_page_text(self):
        self.assertIn("url | use/skip/unjudged | reason", self.p)
        self.assertRegex(self.p, r"page text never reaches")

    def test_no_subagent_fallback(self):
        s = sentences_with(self.raw, "fallback")
        self.assertTrue(s, "no fallback sentence in Step 2.5")
        s = norm(" ".join(s))
        self.assertRegex(s, r"skip")
        self.assertRegex(s, r"read support[^.]*as before")

    def test_no_duplicate_rubric_block(self):
        self.assertNotRegex(self.raw, r"(?m)^\s*>")  # no quoted instruction block
        self.assertNotRegex(self.raw, r"(?s)```.*```")
        rubric = norm(read(RUBRIC_PATH))
        for phrase in ("substantively addresses", "seo filler", "thin rewrite", "first-hand detail",
                       "once more after all the others", "no page text, quotes, or summaries"):
            if phrase in rubric:
                self.assertNotIn(phrase, self.p, phrase)
        self.assertNotIn("reply with exactly one line", self.p)
        self.assertNotRegex(self.raw, r"\b(USE|SKIP|UNJUDGED)\|")

    def test_skip_and_unjudged_final_for_run(self):
        s = norm(" ".join(sentences_with(self.raw, "final")))
        self.assertIn("run", s)
        self.assertIn("skip", self.p)
        self.assertRegex(self.p, r"skip and unjudged[^.]*(stay )?out of step 3")
        self.assertRegex(self.p, r"do not open them|not reopen|never reopen|don't open them")

    def test_page_vs_fetch_error_and_partial_failures(self):
        self.assertIn("`page:`", self.raw)
        self.assertIn("`fetch-error:`", self.raw)
        self.assertRegex(self.p, r"`fetch-error:`[^.]*twice|`fetch-error:`[^.]*(second|again)")
        self.assertRegex(self.p, r"`page:`[^.;]*(site|nothing to judge)")
        self.assertRegex(self.p, r"some (sources )?fail[^.]*still[^.]*verdicts")

    def test_judge_unavailable_definition(self):
        s = norm(" ".join(sentences_with(self.raw, "error")))
        self.assertRegex(self.p, r"errors?\b")
        self.assertRegex(self.p, r"every source is unjudged")
        self.assertRegex(self.p, r"every source is unjudged with `fetch-error`")
        self.assertTrue(s)
        self.assertRegex(self.p, r"read support sources as before")
        self.assertIn("`judge-unavailable`", self.raw)
        self.assertRegex(self.p, r"`judge-unavailable`[^.]*`degraded:`")


class SkillStep6ClosingLines(unittest.TestCase):
    def setUp(self):
        self.raw = section(read(SKILL_PATH), "6")
        self.p = norm(self.raw)

    def _block(self):
        blocks = re.findall(r"(?s)```[^\n]*\n(.*?)```", self.raw)
        hit = [b for b in blocks if "Keywords:" in b]
        self.assertEqual(len(hit), 1, blocks)
        return [l.strip() for l in hit[0].splitlines() if l.strip()]

    def test_three_lines_in_order(self):
        self.assertRegex(self.p, r"three lines[^.]*order")
        lines = self._block()
        self.assertEqual(len(lines), 3, lines)
        self.assertRegex(lines[0], r"^n sources collected → m passed → j judged-out → l read$")
        self.assertRegex(lines[1], r"^Degraded:")
        self.assertRegex(lines[2], r"^Keywords:")

    def test_degraded_always_present_none_only_when_clean(self):
        self.assertRegex(self.p, r"`degraded:` is always")
        self.assertRegex(self.p, r"`none` only when nothing")
        self.assertIn("` · `", self.raw)
        self.assertRegex(self.p, r"summed over all rounds")

    def test_degraded_items_exact_with_meanings(self):
        bullets = re.findall(r"(?m)^\s*-\s+`([^`]+)`:\s*(.+)$", self.raw)
        tokens = [b[0] for b in bullets]
        self.assertEqual(tokens, [
            "step1-inline <k>/<n>",
            "lookups-failed <f>/<t>",
            "judge-unavailable",
            "judge-skipped",
            "unjudged <u> (fetch-error <f>, page <p>, missing <m>)",
            "verify-skipped",
        ])
        m = {t.split()[0]: norm(d) for t, d in bullets}
        self.assertRegex(m["step1-inline"], r"search subagent")
        self.assertRegex(m["step1-inline"], r"records")
        self.assertRegex(m["step1-inline"], r"instead of[^.]*file")
        self.assertIn("srcscore", m["lookups-failed"])
        self.assertIn("stderr", m["lookups-failed"])
        self.assertIn("unreachable", m["lookups-failed"])
        self.assertIn("lookup", m["lookups-failed"])
        self.assertIn("step 2.5", m["judge-unavailable"])
        self.assertRegex(m["judge-unavailable"], r"no verdicts")
        self.assertRegex(m["judge-unavailable"], r"launched")
        self.assertRegex(m["judge-unavailable"], r"no subagent")
        self.assertIn("step 2.5", m["judge-skipped"])
        self.assertRegex(m["judge-skipped"], r"subagent could be launched")
        self.assertIn("missing", m["unjudged"])
        self.assertIn("academic", m["verify-skipped"])
        self.assertIn("support", m["unjudged"])
        self.assertIn("unjudged", m["unjudged"])
        self.assertIn("reason", m["unjudged"])


class CrossReferences(unittest.TestCase):
    def setUp(self):
        self.skill = read(SKILL_PATH)

    def test_step1_inline_fallback_points_to_degraded(self):
        s1 = section(self.skill, "1")
        para = [p for p in re.split(r"\n\s*\n", s1) if "returns the records" in p or "could not write" in p]
        self.assertTrue(para, "inline-records fallback paragraph not found in Step 1")
        self.assertIn("`step1-inline`", para[0])
        self.assertIn("`Degraded:`", para[0])

    def test_no_dangling_degraded_names(self):
        step6 = section(self.skill, "6")
        defined = set(re.findall(r"(?m)^\s*-\s+`([a-z0-9-]+)", step6))
        self.assertEqual(defined, set(DEGRADED_ITEMS))
        for step in ("1", "2.5"):
            sec = section(self.skill, step)
            for tok in re.findall(r"`([a-z0-9]+(?:-[a-z0-9]+)+)`", sec):
                if tok in ("fetch-error",):
                    continue  # an UNJUDGED reason kind, not a Degraded item
                if tok in DEGRADED_ITEMS or re.search(r"`%s`[^.]*degraded" % re.escape(tok), sec, re.I):
                    self.assertIn(tok, defined, "Step %s names Degraded item %r not in Step 6" % (step, tok))
            # any token placed "in the report's `Degraded:` line" must be defined
            for tok in re.findall(r"`([a-z0-9-]+)`[^.`]*(?:`[^`]*`[^.`]*)?`Degraded:`", sec):
                self.assertIn(tok, defined, "Step %s: %r" % (step, tok))

    def test_pending_evals_mentions_degraded_and_reason_split(self):
        raw = read(PENDING_PATH)
        lines = [l for l in raw.splitlines() if "arms.py" in l and "Degraded:" in l]
        self.assertTrue(lines, "pending-evals does not name `Degraded:` with arms.py")
        self.assertRegex(lines[0], r"(파싱|pars)")
        self.assertIn("page:", raw)
        self.assertIn("fetch-error:", raw)


if __name__ == "__main__":
    unittest.main()
