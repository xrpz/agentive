# agentive-unit: selftest.component
# design: docs/IMPLEMENTATION.md §4 (ayaml subset), §6 (tests)
"""ayaml must agree with PyYAML (YAML 1.2 core resolution aside) on the subset agentive uses."""
import math
import os
import sys
import unittest

import yaml  # dev-time only (requirements-dev.txt); the run time never imports PyYAML

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "..", "src", "repo-layer", "dot-agents", "lib"))
from agentive import ayaml  # noqa: E402

WORKFLOW = """\
name: agentive-review-gate
"on":
  pull_request:
    types: [opened, synchronize, reopened, edited]
  pull_request_review_comment:
    types: [created, edited, deleted]
permissions:
  contents: read
  pull-requests: write
jobs:
  gate:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
        with:
          fetch-depth: 0
      - name: Run gate   # comment
        run: |
          python3 .agents/bin/agentive-review ci "${{ github.event.pull_request.number }}"
          echo done

      - run: 'echo "a: b"'
        env: {A: "1", B: 'x''y'}
"""

DOCS = [
    WORKFLOW,
    "a: 1\nb: 1.5\nc: true\nd: null\ne: ~\nf: 2.1.296\ng: '007'\nh: \"q\\tq\"\ni: -3\nj: 0x1F\n",
    "list:\n- a\n- b: 1\n  c: [1, 2, {d: e}]\n-\n  - x\n  - y\nempty: []\nem: {}\n",
    "k: >\n  folded one\n  two\n\n  para\nl: |-\n  keep\n    indent\nm: |+\n  x\n\nn: after\n",
    "verbs:\n  test:\n    status: pending\n    reason: no_evidence\npaths:\n  src: []\n",
    "- id: push-force\n  decision: deny\n  match: {kind: argv_prefix, value: [git, push, --force]}\n  reason: \"x. y\"\n",
    "key with spaces: v\n\"quoted: key\": 3\nurl: https://example.com/a#frag\n",
    "multi: \"line one\n  line two\"\n",
    "flow: [a, b,\n  c]\n",
    "---\nx: 1\n",
    "",
    "# only a comment\n",
    "s: 한국어 값\nt: 'it''s'\n",
]

BAD = [
    ("a: &x 1\nb: *x\n", "anchors"),
    ("a: 1\na: 2\n", "duplicate"),
    ("a: b: c\n", "': '"),
    ("x: 1\n---\ny: 2\n", "several documents"),
    ("a:\n\t- x\n", "tab"),
    ("a: plain\n  continued\n", "multi-line plain"),
    ("? complex\n: v\n", "complex"),
]


def norm(v):
    """PyYAML resolves YAML 1.1 (on/yes/off, 0123 octal); agentive files never rely on that."""
    if isinstance(v, float) and math.isnan(v):
        return "nan"
    if isinstance(v, dict):
        return {norm(k): norm(x) for k, x in v.items()}
    if isinstance(v, list):
        return [norm(x) for x in v]
    return v


class AyamlTest(unittest.TestCase):
    def test_agrees_with_pyyaml(self):
        for doc in DOCS:
            with self.subTest(doc=doc[:40]):
                self.assertEqual(norm(ayaml.loads(doc)), norm(yaml.safe_load(doc)))

    def test_on_key_stays_a_string(self):
        self.assertIn("on", ayaml.loads("on:\n  push: {}\n"))

    def test_roundtrip(self):
        for doc in DOCS:
            with self.subTest(doc=doc[:40]):
                v = ayaml.loads(doc)
                text = ayaml.dumps(v)
                self.assertEqual(norm(ayaml.loads(text)), norm(v), text)
                self.assertEqual(norm(yaml.safe_load(text)), norm(v), text)

    def test_dump_values(self):
        v = {"a": "1.0", "b": "true", "c": "", "d": "x: y", "e": "multi\nline\n", "f": [{"g": 1, "h": [1]}],
             "i": "- dash", "j": "${{ github.sha }}", "k": "한국어", "l": None, "m": 2.5, "n": "#x", "o": "a #b"}
        text = ayaml.dumps(v)
        self.assertEqual(ayaml.loads(text), v, text)
        self.assertEqual(yaml.safe_load(text), v, text)

    def test_errors_name_the_line(self):
        for doc, what in BAD:
            with self.subTest(doc=doc):
                with self.assertRaises(ayaml.YAMLError) as cm:
                    ayaml.loads(doc)
                self.assertRegex(str(cm.exception), r"^ayaml: line \d+: ")
                self.assertIn(what, str(cm.exception))


if __name__ == "__main__":
    unittest.main()
