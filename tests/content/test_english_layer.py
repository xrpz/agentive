# agentive-unit: selftest.component
# design: decisions.accepted[ENGLISHLAYER]
"""ENGLISHLAYER (user decision 2026-10-29): everything installed into a user repo or printed at run time is English.
No Hangul in src/repo-layer, src/engine, src/plugin; the old Korean markers and labels are gone from the generated payloads."""
import os
import re
import sys
import unittest

from helpers import ROOT

HANGUL = re.compile("[\\uac00-\\ud7a3\\u1100-\\u11ff\\u3130-\\u318f]")
SCOPE = ["src/repo-layer", "src/engine", "src/plugin"]
ALLOW = {}   # path -> reason; nothing is allowed


def text_files(base):
    for r, ds, fs in os.walk(base):
        ds[:] = [d for d in ds if d != "__pycache__"]
        for f in fs:
            p = os.path.join(r, f)
            try:
                with open(p, encoding="utf-8") as fh:
                    yield p, fh.read()
            except UnicodeDecodeError:
                continue   # binary (icon.png)


class EnglishLayer(unittest.TestCase):
    def test_no_hangul_in_installed_sources(self):
        hits = []
        for s in SCOPE:
            for p, t in text_files(os.path.join(ROOT, s)):
                rel = os.path.relpath(p, ROOT)
                if rel in ALLOW:
                    continue
                for n, line in enumerate(t.splitlines(), 1):
                    if HANGUL.search(line):
                        hits.append(f"{rel}:{n}")
        self.assertEqual(hits, [], "Hangul in installed sources; translate to English: " + ", ".join(hits[:10]))

    def test_no_hangul_in_generated_payloads(self):
        hits = []
        base = os.path.join(ROOT, "plugins")
        if os.path.isdir(base):
            for p, t in text_files(base):
                if HANGUL.search(t):
                    hits.append(os.path.relpath(p, ROOT))
        self.assertEqual(hits, [], "run python3 tools/generate.py; Hangul in generated plugin files: " + ", ".join(hits[:10]))

    def test_old_markers_are_gone(self):
        for s in SCOPE:
            for p, t in text_files(os.path.join(ROOT, s)):
                self.assertNotIn("\uc124\uacc4 \ud310\ub2e8", t, p)
                self.assertNotIn("\ubbf8\uac80\uc99d", t, p)


if __name__ == "__main__":
    unittest.main()
