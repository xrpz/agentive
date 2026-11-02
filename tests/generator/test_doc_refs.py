# agentive-unit: selftest.component
# design: decisions.accepted[IMPLSPLIT], docs/IMPLEMENTATION.md §5 (item 24: section references)
"""Section references into docs/IMPLEMENTATION.md (sections 1 to 9) and docs/impl/decisions-log.md (sections 10 to 35).

A reference that names a section which does not exist, and a contents list that misses a heading, fail here. The generator only
checks that a referenced file exists, so a moved or renumbered section would otherwise break its references without a message."""
import os
import re
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
IMPL = "docs/IMPLEMENTATION.md"
LOG = "docs/impl/decisions-log.md"
SKIP_DIRS = {".git", "plugins", "node_modules", "__pycache__", "map"}   # plugins/ and docs/map/ are generated copies
TEXT_EXT = {".py", ".md", ".yaml", ".yml", ".json", ".sh", ".txt", ".toml", ".rules", ".html"}

MORE = r"(?P<more>(?:\s*(?:,|and|/)\s*§\d+(?:\.\d+)?)*)"
QUALIFIED = re.compile(r"(?P<who>docs/impl/decisions-log(?:\.md)?|decisions-log(?:\.md)?|(?:docs/)?IMPLEMENTATION(?:\.md)?)\s+§(?P<n>\d+)(?:\.\d+)?" + MORE)
OTHER_FILE = re.compile(r"docs/impl/[A-Z]\.md\s+§\d+(?:\.\d+)?" + MORE)
HEADING = re.compile(r"^## (\d+)\. ", re.M)
CONTENTS = re.compile(r"^- §(\d+)(?: to §(\d+))?", re.M)


def read(rel):
    with open(os.path.join(ROOT, rel), encoding="utf-8") as f:
        return f.read()


def headings(rel):
    return [int(n) for n in HEADING.findall(read(rel))]


def contents(rel):
    out = []
    for a, b in CONTENTS.findall(read(rel)):
        out += list(range(int(a), int(b) + 1)) if b else [int(a)]
    return out


def repo_text_files():
    for base, dirs, files in os.walk(ROOT):
        dirs[:] = sorted(d for d in dirs if d not in SKIP_DIRS)
        for name in sorted(files):
            if os.path.splitext(name)[1] in TEXT_EXT:
                rel = os.path.relpath(os.path.join(base, name), ROOT).replace(os.sep, "/")
                yield rel


class SectionNumbers(unittest.TestCase):
    def test_the_two_files_split_the_numbers(self):
        impl, log = headings(IMPL), headings(LOG)
        self.assertEqual(impl, list(range(1, 10)), "docs/IMPLEMENTATION.md keeps sections 1 to 9 in order")
        self.assertEqual(log, list(range(10, 10 + len(log))), "docs/impl/decisions-log.md continues at 10 without a gap")
        self.assertTrue(log, "the log is not empty")

    def test_contents_lists_match_the_headings(self):
        impl, log = headings(IMPL), headings(LOG)
        self.assertEqual(contents(IMPL), impl + log, "docs/IMPLEMENTATION.md contents: every section 1 to 9, then the log range")
        self.assertEqual(contents(LOG), log, "docs/impl/decisions-log.md contents lists each of its sections once, in order")


class References(unittest.TestCase):
    def setUp(self):
        self.known = {"impl": set(headings(IMPL)), "log": set(headings(LOG))}

    @staticmethod
    def kind(who):
        return "log" if "decisions-log" in who else "impl"

    def test_qualified_references_resolve(self):
        bad = []
        for rel in repo_text_files():
            text = read(rel)
            for m in QUALIFIED.finditer(text):
                k = self.kind(m.group("who"))
                nums = [int(m.group("n"))] + [int(x) for x in re.findall(r"§(\d+)", m.group("more"))]
                for n in nums:
                    if n not in self.known[k]:
                        line = text.count("\n", 0, m.start()) + 1
                        bad.append(f"{rel}:{line}: {m.group(0).strip()!r} names no section {n} of {IMPL if k == 'impl' else LOG}")
        self.assertEqual(bad, [], "fix the section number or the file name (sections 1 to 9: " + IMPL + ", 10 and up: " + LOG + ")")

    def test_bare_references_stay_inside_their_own_file(self):
        bad = []
        for rel, k in ((IMPL, "impl"), (LOG, "log")):
            text = "\n".join(ln for ln in read(rel).splitlines() if not ln.startswith("- §"))   # the contents list is checked above
            text = QUALIFIED.sub(" ", OTHER_FILE.sub(" ", text))
            for m in re.finditer(r"§(\d+)", text):
                if int(m.group(1)) not in self.known[k]:
                    line = text.count("\n", 0, m.start()) + 1
                    bad.append(f"{rel}: bare §{m.group(1)} (text line {line}) is not a section of this file; write the file name")
        self.assertEqual(bad, [])

    def test_an_unknown_section_is_caught(self):
        """The check itself: a reference to a section that is in neither file is reported."""
        m = QUALIFIED.search("see " + "docs/IMPLEMENTATION" + ".md §99 for it")
        self.assertIsNotNone(m)
        self.assertNotIn(int(m.group("n")), self.known["impl"] | self.known["log"])
        m = QUALIFIED.search("(" + "decisions-log" + " §23, §29)")
        self.assertEqual((m.group("n"), re.findall(r"§(\d+)", m.group("more"))), ("23", ["29"]))


if __name__ == "__main__":
    unittest.main()
