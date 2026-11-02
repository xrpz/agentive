# agentive-unit: selftest.component
# design: decisions.accepted[LOCALTIER], decisions.accepted[DEPHUMAN], spec.review_pipeline.reviewer_eval.ci, docs/impl/decisions-log.md §22
"""End-to-end recheck step 2 (decision e2e_28_31, user 2026-10-31): E2E-28 the summary comment's reviewer-eval line is short
and the details stay in the reviewer-eval comment; E2E-31 the dependency reason is one short sentence, shown once in the
summary comment (the tier line names only its code). E2E-29 / E2E-30 (the local estimate) are tested in
tests/content/test_prbody.py."""
import json
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import helpers as h  # noqa: E402,F401
import test_stages as ts  # noqa: E402
from test_e2e_batch_d import summary  # noqa: E402
from test_e2e_batch_e import CiRun  # noqa: E402
from test_labels import load_bin  # noqa: E402

from agentive.review import evals, post, rv1  # noqa: E402

DC = {"added": ["pypi:humanize"], "removed": [], "changed": [], "lockfiles": ["uv.lock"], "unreadable": [], "package_files": ["pyproject.toml"]}


class E2E31DependencyReasonTest(unittest.TestCase):
    decide = ts.GateTest.decide

    def test_e2e_31_dependency_reason_is_one_short_sentence(self):
        self.assertEqual(rv1.dependency_change_text(DC), "human review needed: dependency change (added pypi:humanize; lockfile uv.lock), "
                                                         "never auto-merged.")
        ci = rv1.dependency_change_text(DC, None)
        self.assertEqual(ci, "human review needed: dependency change (added pypi:humanize; lockfile uv.lock), never auto-merged; "
                             "dependency review did not run for this commit (information only).")
        self.assertLess(len(ci), 190)                       # was about 400 characters in the E2E recheck (#14)
        for gone in ("supply-chain review", "dependency graph off", "Code Security", "Every dependency or lockfile change"):
            self.assertNotIn(gone, ci)
        self.assertIn("dependency review ran and found no critical CVE (information only)", rv1.dependency_change_text(DC, "pass"))

    def test_e2e_31_summary_comment_shows_the_reason_once(self):
        detail = rv1.dependency_change_text(DC, None)
        rv4 = {"tier": 2, "reasons": ["dependency_change", "dependency_change: " + detail]}
        r = self.decide(tier=2, rv4_reasons=rv4["reasons"], explainer=({"section": True}, []))
        self.assertEqual(r["decision"], "human_required")
        text = post.summary_comment(summary(r, rv4=rv4), 0, {})
        self.assertEqual(text.count(detail), 0, text)                        # E2E-31b: the sentence is in Why, never twice
        visible = text.split("<details>")[0]                                   # COMMENTUX: G6 lines, code only in Details
        self.assertIn("needs a human review — dependency change", visible)
        self.assertIn("- Dependencies changed: added pypi:humanize; lockfile uv.lock.\n", visible)
        self.assertIn("- Dependency review did not run for this commit (information only).\n", visible)
        self.assertNotIn("dependency_change", visible)
        self.assertNotIn("never auto-merged", text)                           # the rule sentence is neither in Why nor in Details
        # a reason that the not-merged list does not show keeps its sentence on the tier line (E2E-24 unchanged)
        self.assertEqual(post.tier_reasons(["always_human", "always_human: env var added or removed: TOKEN"], []),
                         ["always_human: env var added or removed: TOKEN"])
        self.assertEqual(post.tier_reasons(rv4["reasons"], post.reason_lines(rv4["reasons"])), ["dependency_change"])

    def test_e2e_31b_details_keep_the_lists_and_the_code_only(self):
        """E2E-31 rest (#6): the reason sentence is in Why once; Details holds the reason code and the full lists (Why shows
        five names), with no second sentence."""
        names = [f"pypi:pkg{i}" for i in range(8)]
        dc = dict(DC, added=names, changed=[{"name": "pypi:old", "from": "1", "to": "2"}], removed=["pypi:gone"])
        detail = rv1.dependency_change_text(dc, "pass")
        rv4 = {"tier": 2, "reasons": ["dependency_change", "dependency_change: " + detail]}
        r = self.decide(tier=2, rv4_reasons=rv4["reasons"], explainer=({"section": True}, []))
        rep = summary(r, rv4=rv4)
        rep["rv1"] = {"signals": {"dependency_changes": dc}}
        text = post.summary_comment(rep, 0, {})
        visible, details = text.split("<details>", 1)[0], text.split("<details>", 2)[-1]
        self.assertIn("- Dependencies changed: added pypi:pkg0, pypi:pkg1, pypi:pkg2, pypi:pkg3, pypi:pkg4 and 3 more;", visible)
        self.assertEqual(text.count("Dependencies changed"), 1)
        self.assertEqual(text.count("found no critical CVE"), 1)
        line = [x for x in details.splitlines() if x.startswith("- Dependency change lists:")]
        self.assertEqual(len(line), 1, details)
        self.assertIn("added " + ", ".join(f"`{n}`" for n in names), line[0])
        self.assertIn("changed `pypi:old`", line[0])
        self.assertIn("removed `pypi:gone`", line[0])
        self.assertIn("lockfiles `uv.lock`", line[0])
        self.assertIn("`dependency_change`", details)                        # the code stays in Reason codes
        self.assertNotIn("human review needed", text)
        self.assertNotIn("never auto-merged", text)

    def test_dep_review_text_says_what_happened_in_plain_words(self):
        """The dependency-review state in plain words: ran and found nothing / found a critical CVE / could not run (the action
        did not complete) / did not run (no entry); information only, never the deciding reason (DEPHUMAN)."""
        want = {"pass": "dependency review ran and found no critical CVE (information only)",
                "fail": "dependency review found a critical CVE (see the dependency-review job)",
                "skipped": "dependency review could not run (usually the dependency graph is off, or a private repository has no "
                           "GitHub Code Security) (information only)",
                None: "dependency review did not run for this commit (information only)"}
        for state, text in want.items():
            got = rv1.dependency_change_text(DC, state)
            self.assertTrue(got.endswith("; " + text + "."), (state, got))
            self.assertNotIn("no result", got)
            rv4 = {"tier": 2, "reasons": ["dependency_change", "dependency_change: " + got]}
            r = self.decide(tier=2, rv4_reasons=rv4["reasons"], explainer=({"section": True}, []))
            vis = post.summary_comment(summary(r, rv4=rv4), 0, {}).split("<details>")[0]
            self.assertIn("- " + text[0].upper() + text[1:] + ".\n", vis)
        self.assertNotIn("no result", "".join(rv1.DEP_REVIEW_TEXT.values()))

    def test_dep_review_text_the_checks_helper_tells_unavailable_from_not_run(self):
        """The workflow step prints status=unavailable when the action did not complete; the checks helper records it as a
        skipped dependency-review check, while an empty or skipped job result (the job did not run) adds no entry."""
        mod = load_bin()
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            out = os.path.join(d, "c.json")
            for res, want in (("unavailable", [{"name": "dependency-review", "status": "skipped"}]), ("pass", [{"name": "dependency-review", "status": "pass"}]),
                              ("", []), ("skipped", [])):
                ns = mod.argparse.Namespace(out=out, outer=None, job=[f"dependency-review={res}"], must_run=None)
                mod.cmd_checks(ns)
                self.assertEqual(json.loads(h.read(out)), want, res)
        text = h.read(os.path.join(h.SRC, "dot-github", "workflows", "agentive-review.yml"))
        self.assertIn("s=unavailable", text)


class E2E28ReviewerEvalLineTest(CiRun):
    def test_e2e_28_summary_line_is_short_and_the_comment_keeps_the_details(self):
        self.repo.write({"code_review.md": h.read(self.repo.path("code_review.md")) + "\nOne more rule.\n"})
        head = self.repo.commit("rule")
        line = evals.summary_line(self.repo.dir, self.base, head)
        self.assertTrue(line.startswith("not scored (evals/reviewer/ has no case yet);"), line)        # COMMENTUX: a clause
        self.assertLess(len(line), 220)                     # was about 520 characters with nested parentheses
        self.assertEqual(line.count("("), 1)
        self.assertIn("`eval-override: <reason>`", line)
        self.assertTrue(line.endswith("details in the reviewer-eval comment."), line)
        text = evals.comment(evals.summarize([], {}, {}, 3, "b" * 40, "c" * 40), ["code_review.md"])
        for kept in (evals.OVERRIDE_FACT, evals.CASES_HELP, "for example `eval-override: no eval cases yet; reviewed by hand`"):
            self.assertIn(kept, text)
            self.assertNotIn(kept, line)


if __name__ == "__main__":
    unittest.main()
