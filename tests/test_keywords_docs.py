import unittest
from pathlib import Path

SKILL = Path(__file__).resolve().parent.parent / "skills" / "scored-web-search"


class KeywordsDocs(unittest.TestCase):
    def test_skill_step6_keywords_after_summary(self):
        md = (SKILL / "SKILL.md").read_text()
        i = md.find("n sources collected → m passed → j judged-out → l read")
        k = md.find("Keywords: <round>: q1, q2 / <round>: q3")
        self.assertGreater(i, -1)
        self.assertGreater(k, i)


if __name__ == "__main__":
    unittest.main()
