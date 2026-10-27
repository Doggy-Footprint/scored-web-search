"""Independent spec tests: Step 1 searcher contract agrees between SKILL.md and the searcher prompt.

The subagent writes its records to a file the main agent names and replies with only
path + count; the inline JSON array is a fallback for when it cannot write the file.
"""

import os
import re
import unittest

import _pathsetup  # noqa: F401

ROOT = _pathsetup.ROOT
SKILL = os.path.join(ROOT, "skills", "scored-web-search")
PROMPT_PATH = os.path.join(SKILL, "references", "searcher-prompt.md")
SKILL_PATH = os.path.join(SKILL, "SKILL.md")


def read(p):
    with open(p, encoding="utf-8") as f:
        return f.read()


def norm(text):
    return re.sub(r"\s+", " ", text).lower()


def step1_section(skill_text):
    m = re.search(r"(?ms)^#{2,4} *Step 1\b.*?(?=^#{2,4} *Step \d)", skill_text)
    assert m, "Step 1 section not found in SKILL.md"
    return m.group(0)


def paragraphs(text):
    return [p for p in re.split(r"\n\s*\n", text) if p.strip()]


def lines_mentioning(text, *needles):
    return [l for l in text.splitlines() if any(n in l.lower() for n in needles)]


class SearcherPromptContract(unittest.TestCase):
    def setUp(self):
        self.raw = read(PROMPT_PATH)
        self.p = norm(self.raw)

    def test_given_queries_and_file_path(self):
        self.assertIn("quer", self.p)
        self.assertRegex(self.p, r"(file path|path)")
        self.assertRegex(self.p, r"given[^.]*quer[^.]*path|given[^.]*path[^.]*quer")

    def test_websearch_all_queries_single_turn(self):
        self.assertIn("websearch", self.p)
        self.assertRegex(self.p, r"(single|one) turn")
        self.assertRegex(self.p, r"all (the |of the )?quer")

    def test_writes_json_array_with_url_title_optional_date(self):
        self.assertRegex(self.p, r"write[^.]*(path|file)")
        self.assertIn("json array", self.p)
        for field in ('"url"', '"title"', '"date"'):
            self.assertIn(field, self.raw)
        self.assertRegex(self.raw, r'"date"\s*\?|optional[^.]*date|date[^.]*optional|only when')

    def test_date_iso_from_search_result_never_guessed(self):
        date_lines = norm(" ".join(lines_mentioning(self.raw, "date")))
        self.assertIn("iso", date_lines)
        self.assertRegex(date_lines, r"search result")
        self.assertRegex(date_lines, r"never guess|do not guess|don't guess|not guess")

    def test_no_summaries_snippets_commentary_never_open_pages(self):
        for word in ("summar", "snippet", "commentary"):
            self.assertRegex(self.p, r"\bno\b[^.]*" + word, word)
        self.assertRegex(self.p, r"(never|do not|don't) open (the )?pages?")

    def test_reply_is_only_path_and_count(self):
        reply_lines = lines_mentioning(self.raw, "reply", "respond", "return")
        joined = norm(" ".join(reply_lines))
        self.assertRegex(joined, r"only[^.]*path[^.]*count|only[^.]*count[^.]*path")
        self.assertRegex(joined, r"record count|count")

    def test_fallback_inline_json_when_file_cannot_be_written(self):
        fb = [l for l in self.raw.splitlines()
              if re.search(r"(cannot|can't|can not|unable to|fail\w* to)[^.]*write", l.lower())]
        self.assertTrue(fb, "no fallback line for when the file cannot be written")
        fb_text = norm(" ".join(fb))
        self.assertIn("json array", fb_text)
        self.assertRegex(fb_text, r"nothing else|only")

    def test_old_return_only_json_array_contract_gone(self):
        self.assertNotRegex(self.p, r"return only (a |the )?json array")
        # Any line telling it to reply/return the JSON array must be conditioned on the write failure.
        for line in self.raw.splitlines():
            l = line.lower()
            if re.search(r"(return|reply|respond)[^.]*json array", l):
                self.assertRegex(l, r"(cannot|can't|can not|unable|fail)", line)


class SkillStep1Contract(unittest.TestCase):
    def setUp(self):
        self.raw = step1_section(read(SKILL_PATH))
        self.s = norm(self.raw)

    def test_delegates_to_subagent_with_searcher_prompt(self):
        self.assertIn("references/searcher-prompt.md", self.raw)
        self.assertRegex(self.s, r"sub-?agent")

    def test_no_fork_and_lightest_model(self):
        self.assertRegex(self.s, r"(do not|don't|never|not) use `?fork`?")
        self.assertRegex(self.s, r"lightest model|haiku")

    def test_main_agent_names_temp_file_one_per_subagent(self):
        self.assertRegex(self.s, r"(file path|path)[^.]*(you|main agent) name|(you|main agent) name[^.]*(file|path)")
        self.assertIn("$tmpdir", self.s)
        self.assertRegex(self.s, r"one file per sub-?agent")

    def test_records_url_title_optional_iso_date(self):
        for field in ("`url`", "`title`", "`date`"):
            self.assertIn(field, self.raw)
        self.assertRegex(self.s, r"optional[^.]*iso[^.]*date|iso[^.]*date[^.]*optional")
        self.assertRegex(self.s, r"date`? taken from the search result|from (the )?search results|supplied by search results")

    def test_returns_only_path_and_count(self):
        self.assertRegex(self.s, r"returns? only the (file )?path and the (record )?count")

    def test_no_snippets_summaries_no_page_open(self):
        self.assertRegex(self.s, r"no summaries")
        self.assertRegex(self.s, r"no snippets")
        self.assertRegex(self.s, r"(do not|don't|never) open (the )?pages?")

    def test_inline_fallback_main_agent_writes_and_reports(self):
        fb = [p for p in paragraphs(self.raw)
              if re.search(r"(could not|cannot|can't|unable to|fail\w* to) write", p.lower())]
        self.assertTrue(fb, "no inline-fallback paragraph in Step 1")
        t = norm(" ".join(fb))
        self.assertRegex(t, r"returns? the records|records themselves|json array")
        self.assertRegex(t, r"write (them|the records)[^.]*path you named|path you named")
        self.assertRegex(t, r"\bgo on\b|continue")
        self.assertRegex(t, r"report[^.]*inline|inline[^.]*report")

    def test_no_subagent_fallback_paragraph_kept(self):
        fb = [p for p in paragraphs(self.raw) if re.search(r"no sub[- ]?agent fallback", p.lower())]
        self.assertEqual(len(fb), 1, "expected exactly one 'No sub agent fallback' paragraph in Step 1")
        t = norm(fb[0])
        self.assertRegex(t, r"main agent[^.]*(calls|runs|uses)[^.]*(web-?search|search)")
        self.assertIn("urls.json", t)

    def test_no_subagent_fallback_distinct_from_inline_fallback(self):
        for p in paragraphs(self.raw):
            l = p.lower()
            if re.search(r"no sub[- ]?agent fallback", l):
                self.assertNotIn("inline", l)


class ContractsAgree(unittest.TestCase):
    """Neither file may describe the normal reply as the records themselves."""

    def test_both_files_say_path_and_count(self):
        prompt = norm(read(PROMPT_PATH))
        step1 = norm(step1_section(read(SKILL_PATH)))
        for t in (prompt, step1):
            self.assertRegex(t, r"path[^.]*(and|\+)[^.]*count")

    def test_skill_step1_does_not_claim_subagent_returns_json_normally(self):
        step1 = step1_section(read(SKILL_PATH))
        for line in step1.splitlines():
            l = line.lower()
            if re.search(r"returns? only (a |the )?json", l):
                self.fail("Step 1 still says the subagent returns only JSON: " + line)


if __name__ == "__main__":
    unittest.main()
