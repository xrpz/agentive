# agentive-unit: selftest.component
# design: decisions.accepted[EVALBUNDLE], decisions.accepted[RVEVALRUN], decisions.accepted[COMMENTUX], spec.review_pipeline.reviewer_eval.scoring, spec.review_pipeline.reviewer_eval.ci, contracts[reviewer-eval-case]
"""EVALBUNDLE (user 2026-11-01, "일단 그렇게 진행해줘"): the line tolerance of expected ranges, the wrong-place diagnostic, the optional
case fields and the changed-case table of the reviewer-eval comment."""
import copy
import importlib.machinery
import importlib.util
import json
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import helpers as h  # noqa: E402

from agentive import ayaml  # noqa: E402
from agentive.review import evals, post, util  # noqa: E402

SUMMARY_REF = "https://fw.local/schemas/reviewer-eval-case.schema.json#/$defs/summary"
EVAL = ".agents/bin/agentive-reviewer-eval"


def pos(cid="p1", file="a.py", a=10, b=12, **kw):
    c = {"id": cid, "pr": "#1", "label": "positive", "origin": {"kind": "revert", "ref": "#2"},
         "expected_findings": [{"file": file, "line_from": a, "line_to": b}]}
    c.update(kw)
    return c


def neg(cid="n1", **kw):
    c = {"id": cid, "pr": "#3", "label": "negative", "forbidden_findings": [{"file": "b.py", "line_from": 10, "line_to": 12}]}
    c.update(kw)
    return c


def fnd(file, line, blocking=True, bug_class=None, instances=None):
    return {"file": file, "line": line, "instances": instances or [], "blocking": blocking, "bug_class": bug_class}


class SlackTest(unittest.TestCase):
    def test_eval_bundle_positive_slack_boundaries(self):
        """Default slack 2: [from - 2, to + 2] is caught, one line further is not; both ends."""
        self.assertEqual(evals.DEFAULT_SLACK, 2)
        c = pos(a=10, b=12)
        for line, caught in ((7, False), (8, True), (9, True), (10, True), (12, True), (13, True), (14, True), (15, False)):
            self.assertEqual(evals.run_result(c, [fnd("a.py", line)])[0], caught, line)

    def test_eval_bundle_per_range_slack_overrides_the_default(self):
        strict = pos(expected_findings=[{"file": "a.py", "line_from": 10, "line_to": 12, "slack": 0}])
        self.assertFalse(evals.run_result(strict, [fnd("a.py", 9)])[0])
        self.assertTrue(evals.run_result(strict, [fnd("a.py", 10)])[0])
        self.assertFalse(evals.run_result(strict, [fnd("a.py", 13)])[0])
        wide = pos(expected_findings=[{"file": "a.py", "line_from": 10, "line_to": 12, "slack": 5}])
        self.assertTrue(evals.run_result(wide, [fnd("a.py", 5)])[0])
        self.assertTrue(evals.run_result(wide, [fnd("a.py", 17)])[0])
        self.assertFalse(evals.run_result(wide, [fnd("a.py", 18)])[0])

    def test_eval_bundle_instances_and_files(self):
        c = pos(a=10, b=12)
        self.assertTrue(evals.run_result(c, [fnd("z.py", 1, instances=[{"file": "a.py", "line": 14}])])[0])      # instance, with slack
        self.assertFalse(evals.run_result(c, [fnd("z.py", 11)])[0])                                               # right line, other file
        two = pos(expected_findings=[{"file": "a.py", "line_from": 10, "line_to": 12}, {"file": "c.py", "line_from": 5, "line_to": 5}])
        self.assertFalse(evals.run_result(two, [fnd("a.py", 11)])[0])                                             # several ranges stay all-required
        self.assertTrue(evals.run_result(two, [fnd("a.py", 11), fnd("c.py", 6)])[0])

    def test_eval_bundle_negatives_have_slack_zero(self):
        n = neg()      # forbidden b.py:10-12
        self.assertFalse(evals.run_result(n, [fnd("b.py", 11, blocking=False)])[0])
        self.assertFalse(evals.run_result(n, [fnd("b.py", 10, blocking=False)])[0])
        self.assertFalse(evals.run_result(n, [fnd("b.py", 12, blocking=False)])[0])
        for line in (9, 13, 8, 14):                     # inside the positive tolerance, outside the forbidden range: still clean
            self.assertTrue(evals.run_result(n, [fnd("b.py", line, blocking=False)])[0], line)
        self.assertFalse(evals.run_result(n, [fnd("b.py", 40, blocking=True)])[0])          # a blocking finding stays noise anywhere
        bad = neg(forbidden_findings=[{"file": "b.py", "line_from": 10, "line_to": 12, "slack": 1}])
        self.assertTrue(evals.validate_case(bad, "n1"))                                      # no slack override on a forbidden range
        self.assertTrue(h.schema_validator("reviewer-eval-case")(bad))


class WrongPlaceTest(unittest.TestCase):
    def test_eval_bundle_wrong_place_is_same_file_other_line(self):
        c = pos(a=10, b=12)
        self.assertTrue(evals.run_wrong_place(c, [fnd("a.py", 40)]))
        self.assertFalse(evals.run_wrong_place(c, [fnd("a.py", 11)]))               # caught, not wrong place
        self.assertFalse(evals.run_wrong_place(c, [fnd("b.py", 11)]))               # other file
        self.assertFalse(evals.run_wrong_place(c, []))                             # no finding
        self.assertFalse(evals.run_wrong_place(c, None))                           # failed run
        self.assertFalse(evals.run_wrong_place(neg(), [fnd("b.py", 40)]))           # negatives have none
        self.assertTrue(evals.run_wrong_place(c, [fnd("z.py", 1, instances=[{"file": "a.py", "line": 40}])]))

    def test_eval_bundle_wrong_place_needs_the_bug_class_when_the_case_has_one(self):
        c = pos(bug_class="error-handling")
        self.assertTrue(evals.run_wrong_place(c, [fnd("a.py", 40, bug_class="error-handling")]))
        self.assertFalse(evals.run_wrong_place(c, [fnd("a.py", 40, bug_class="logic")]))
        self.assertFalse(evals.run_wrong_place(c, [fnd("a.py", 40)]))               # no class on the finding: not shown
        self.assertTrue(evals.run_wrong_place(pos(), [fnd("a.py", 40, bug_class="logic")]))   # the case has none: any class

    def test_eval_bundle_wrong_place_never_changes_pass_or_adoption(self):
        """The same runs with and without a same-file finding give the same pass counts, newly_* and adopt_suggested."""
        p, n = pos(), neg()
        hit = [fnd("a.py", 11)]
        elsewhere = [fnd("a.py", 40)]
        nothing = []
        a = evals.summarize([p, n], {"p1": [hit] * 3, "n1": [[]] * 3}, {"p1": [elsewhere] * 3, "n1": [[]] * 3}, 3, "b" * 40, "c" * 40)
        b = evals.summarize([p, n], {"p1": [hit] * 3, "n1": [[]] * 3}, {"p1": [nothing] * 3, "n1": [[]] * 3}, 3, "b" * 40, "c" * 40)
        self.assertEqual(a["wrong_place"], [{"id": "p1", "baseline_runs": 0, "candidate_runs": 3}])
        self.assertNotIn("wrong_place", b)
        a2 = {k: v for k, v in a.items() if k != "wrong_place"}
        self.assertEqual(a2, b)
        self.assertEqual((a["newly_missed"], a["reason"], a["adopt_suggested"]), (["p1"], "newly_missed", False))
        self.assertEqual(h.validate_ref(SUMMARY_REF, a), [])

    def test_eval_bundle_wrong_place_counts_runs_not_cases(self):
        s = evals.summarize([pos(), neg()], {"p1": [[fnd("a.py", 40)], [fnd("a.py", 11)], None], "n1": [[]] * 3},
                            {"p1": [[fnd("a.py", 40)]] * 3, "n1": [[]] * 3}, 3, "b" * 40, "c" * 40)
        self.assertEqual(s["wrong_place"], [{"id": "p1", "baseline_runs": 1, "candidate_runs": 3}])


class CompatTest(unittest.TestCase):
    OLD_SUMMARY = {
        "baseline_ref": "b" * 40, "candidate_ref": "c" * 40, "n_runs": 3,
        "cases": [{"id": "p1", "label": "positive", "baseline_pass_runs": 3, "candidate_pass_runs": 3},
                  {"id": "n1", "label": "negative", "baseline_pass_runs": 3, "candidate_pass_runs": 3}],
        "positives": {"total": 1, "baseline_mean": 1.0, "candidate_mean": 1.0, "baseline_spread": 0},
        "negatives": {"total": 1, "baseline_mean": 0.0, "candidate_mean": 0.0, "baseline_spread": 0},
        "newly_caught": [], "newly_missed": [], "newly_noisy": [], "adopt_suggested": True, "reason": "no_regression"}

    def test_eval_bundle_old_cases_and_summaries_stay_valid(self):
        v = h.schema_validator("reviewer-eval-case")
        for c in (pos(), neg(), {"id": "n2", "pr": "a..b", "label": "negative"}):
            self.assertEqual(v(c), [])
            self.assertEqual(evals.validate_case(c, c["id"]), [])
        self.assertEqual(h.validate_ref(SUMMARY_REF, self.OLD_SUMMARY), [])
        # an old summary renders: no wrong-place column, no detail, the table shows `-` for the location
        text = evals.comment(self.OLD_SUMMARY, ["code_review.md (code_review.md)"])
        self.assertNotIn("wrong place", text)
        self.assertIn("**Changed cases (0 of 2):** none", text)
        self.assertEqual(ayaml.loads(util.fenced_blocks(text, "yaml agentive-reviewer-eval")[0]), self.OLD_SUMMARY)

    def test_eval_bundle_the_schema_changed_nothing_required(self):
        sch = json.loads(h.read(os.path.join(h.ROOT, "design", "schemas", "reviewer-eval-case.schema.json")))
        self.assertEqual(sch["required"], ["id", "pr", "label"])
        self.assertEqual(sch["$defs"]["summary"]["required"], ["baseline_ref", "candidate_ref", "n_runs", "cases", "positives", "negatives",
                                                              "newly_caught", "newly_missed", "newly_noisy", "adopt_suggested", "reason"])

    def full_case(self):
        return pos(base_sha="0" * 40, head_sha="1" * 40, bug_class="error-handling", note="session may be None",
                   source={"finding_id": "F-1a2b3c4d5e", "reply": "wrong", "range_basis": "proposed"}, created="2026-11-02", drafted_by="distill",
                   expected_findings=[{"file": "a.py", "line_from": 10, "line_to": 12, "slack": 0}])

    def test_eval_bundle_optional_fields_validate_the_same_in_code_and_schema(self):
        v = h.schema_validator("reviewer-eval-case")
        good = self.full_case()
        self.assertEqual(v(good), [])
        self.assertEqual(evals.validate_case(good, "p1"), [])
        bads = [("base_sha", "abc"), ("head_sha", "G" * 40), ("bug_class", "style"), ("note", ""), ("created", "yesterday"),
                ("drafted_by", "bot"), ("source", {}), ("source", {"reply": "silly"}), ("source", {"range_basis": "guess"}), ("source", {"x": 1})]
        for k, val in bads:
            x = copy.deepcopy(good)
            x[k] = val
            self.assertTrue(v(x), (k, val))
            self.assertTrue(evals.validate_case(x, "p1"), (k, val))
        x = copy.deepcopy(good)
        x["expected_findings"][0]["slack"] = 21
        self.assertTrue(v(x))
        self.assertTrue(evals.validate_case(x, "p1"))
        x = copy.deepcopy(good)
        del x["head_sha"]
        self.assertTrue(evals.validate_case(x, "p1"))      # base_sha and head_sha go together (code check; the schema leaves each optional)

    def test_eval_bundle_a_drafted_case_file_loads(self):
        text = ("# agentive-check: eval-412 (from PR #412)\nid: login-null\npr: \"#412\"\nbase_sha: " + "a" * 40 + "\nhead_sha: " + "b" * 40 +
                "\nlabel: positive\norigin: {kind: fix_pr, ref: \"#431\"}\nexpected_findings:\n  - {file: src/auth/session.py, line_from: 40, line_to: 44}\n"
                "bug_class: error-handling\nnote: \"session may be None\"\nsource: {range_basis: proposed}\ncreated: 2026-11-02\ndrafted_by: distill\n")
        doc = ayaml.loads(text)
        self.assertEqual(evals.validate_case(doc, "login-null"), [])
        self.assertEqual(h.schema_validator("reviewer-eval-case")(doc), [])


class CommentLayoutTest(unittest.TestCase):
    """The mock of the design (pr-comment-mock): 12 cases, 3 runs per side, one case worse."""

    def setUp(self):
        self.cases = [pos("login-null-session", "src/auth/session.py", 40, 44), pos("cache-ttl-off-by-one", "src/cache.py", 88, 90)]
        self.cases += [pos(f"bug-{i}", f"src/m{i}.py", 5, 6) for i in range(6)]
        self.cases += [neg(f"clean-{i}", forbidden_findings=[{"file": "src/c.py", "line_from": 1, "line_to": 2}]) for i in range(4)]
        ok = {c["id"]: [[fnd(c["expected_findings"][0]["file"], c["expected_findings"][0]["line_from"])]] * 3
              for c in self.cases if c["label"] == "positive"}
        clean = {c["id"]: [[]] * 3 for c in self.cases if c["label"] == "negative"}
        self.base = dict(ok, **clean)
        self.cand = dict(ok, **clean)
        self.cand["login-null-session"] = [[fnd("src/auth/session.py", 41)], [fnd("src/auth/token.py", 12)], []]
        self.reasons = [".agents/agents/correctness-reviewer.md (.agents/agents/**)", "code_review.md (code_review.md)"]

    def build(self, cases=None, **kw):
        cases = cases or self.cases
        s = evals.summarize(cases, self.base, self.cand, 3, "a1b2c3d4e5f6" + "0" * 28, "9f8e7d6c5b4a" + "0" * 28)
        self.assertEqual(h.validate_ref(SUMMARY_REF, s), [])
        return s, evals.comment(s, self.reasons, detail={"cases": cases, "baseline": self.base, "candidate": self.cand})

    def test_eval_bundle_changed_case_table_and_reference_line(self):
        s, text = self.build()
        # login-null-session: base 3/3; cand run 1 caught (41 is inside 40..44), run 2 other file, run 3 nothing -> 1/3: newly missed
        self.assertEqual(s["newly_missed"], ["login-null-session"])
        vis = text.split("<details>")[0]
        self.assertIn("**Changed cases (1 of 12)**, regressions first; the change column is text, not color", vis)
        self.assertIn("| login-null-session | `src/auth/session.py:40-44` | 3/3 | 1/3 | worse: newly missed |", vis)
        self.assertIn("Same 12 cases on both sides, 3 runs each: compare case by case;", vis)
        self.assertIn("Reference only: fewer than 10 positive cases (8 now)", vis)
        self.assertLessEqual(len(h.visible_lines(text)), 15, "\n".join(h.visible_lines(text)))        # COMMENTUX budget
        self.assertNotIn("cache-ttl-off-by-one", vis)                                                       # unchanged cases stay folded

    def test_eval_bundle_reference_line_goes_away_at_ten_positives(self):
        cases = self.cases + [pos(f"more-{i}", f"src/x{i}.py", 1, 2) for i in range(2)]
        for c in cases[len(self.cases):]:
            self.base[c["id"]] = [[fnd(c["expected_findings"][0]["file"], 1)]] * 3
            self.cand[c["id"]] = [[fnd(c["expected_findings"][0]["file"], 1)]] * 3
        s, text = self.build(cases)
        self.assertEqual(s["positives"]["total"], 10)
        self.assertNotIn("Reference only", text)

    def test_eval_bundle_why_block_shows_each_run(self):
        _, text = self.build()
        self.assertIn("<details><summary>Why: runs of the changed cases</summary>", text)
        why = text.split("<summary>Why: runs of the changed cases</summary>")[1].split("</details>")[0]
        self.assertIn("- login-null-session, expected `src/auth/session.py:40-44`", why)
        self.assertIn("  - before: caught, caught, caught", why)
        self.assertIn("  - after: caught, missed (posted `src/auth/token.py:12`), missed (no finding)", why)
        self.assertNotIn("<details>", text.split("<details>")[0])

    def test_eval_bundle_regressions_come_first_and_the_full_table_is_folded(self):
        self.cand["cache-ttl-off-by-one"] = [[], [], []]            # worse too
        self.base["bug-0"] = [[], [], []]                           # better: newly caught
        self.cand["bug-0"] = [[fnd("src/m0.py", 5)]] * 3
        self.base["clean-0"] = [[fnd("src/c.py", 1, blocking=False)]] * 3     # noisy before, clean after: better
        s, text = self.build()
        vis = text.split("<details>")[0]
        rows = [ln for ln in vis.splitlines() if ln.startswith("| ") and ("worse" in ln or "better" in ln)]
        self.assertEqual([r.split("|")[1].strip() for r in rows], ["cache-ttl-off-by-one", "login-null-session", "bug-0", "clean-0"])
        self.assertIn("better: newly caught", vis)
        self.assertIn("better: newly clean", vis)
        per = text.split("<summary>Per case (12): ")[1]
        self.assertTrue(per.startswith("newly missed 2, newly caught 1, newly noisy 0, 8 unchanged</summary>"))
        self.assertEqual(per.split("</details>")[0].count("\n| "), 1 + 12)          # header + one row per case (the separator starts with `|-`)
        self.assertIn("| clean-3 | clean, nothing at `src/c.py:1-2` | 3/3 | 3/3 | same |", per)
        self.assertEqual(ayaml.loads(util.fenced_blocks(text, "yaml agentive-reviewer-eval")[0]), s)
        self.assertIn("<summary>Reviewer files changed (2): agents 1, review rules 1</summary>", text)

    def test_eval_bundle_wrong_place_is_shown_not_scored(self):
        self.cand["login-null-session"] = [[fnd("src/auth/session.py", 90)]] * 3
        s, text = self.build()
        self.assertEqual(s["wrong_place"], [{"id": "login-null-session", "baseline_runs": 0, "candidate_runs": 3}])
        self.assertEqual((s["reason"], s["adopt_suggested"]), ("newly_missed", False))
        why = text.split("<summary>Why: runs of the changed cases</summary>")[1].split("</details>")[0]
        self.assertIn("after: missed at another line of the same file (posted `src/auth/session.py:90`)", why)
        self.assertIn("| wrong place before → after |", text)
        self.assertIn("| 0/3 → 3/3 |", text)
        self.assertIn("`wrong place` = a finding on the right file at another line; it is not scored", text.split("<details>")[0])
        self.assertLessEqual(len(h.visible_lines(text)), 15)

    def test_eval_bundle_no_changed_case_is_one_line(self):
        self.cand["login-null-session"] = self.base["login-null-session"]
        s, text = self.build()
        self.assertEqual(s["reason"], "no_regression")
        self.assertIn("**Changed cases (0 of 12):** none", text)
        self.assertNotIn("Why: runs of the changed cases", text)

    def test_eval_bundle_not_compared_comments_have_no_table(self):
        for kw in ({"no_baseline": True}, {"not_run": True}, {}):
            s = evals.summarize([], {}, {}, 3, "b" * 40, "c" * 40, **kw)
            text = evals.comment(s, ["code_review.md"])
            self.assertNotIn("Changed cases", text)
            self.assertNotIn("Reference only", text)

    def test_eval_bundle_a_failed_run_is_named(self):
        self.cand["login-null-session"] = [None, [fnd("src/auth/session.py", 41)], [fnd("src/auth/session.py", 41)]]
        s, text = self.build()
        self.assertEqual(s["newly_missed"], [])
        why = text.split("<summary>Why: runs of the changed cases</summary>")[1].split("</details>")[0]
        self.assertIn("after: failed (no report), caught, caught", why)
        self.assertIn("worse: caught less often", text)


class RunnerTest(unittest.TestCase):
    def test_eval_bundle_score_passes_cases_and_runs_to_the_comment(self):
        repo = h.Repo({"src/app.py": "x = 1\n", "code_review.md": "# c\n"})
        try:
            repo.write({"evals/reviewer/divzero.yaml": "id: divzero\npr: \"#1\"\nlabel: positive\norigin: {kind: revert, ref: \"#2\"}\n"
                                                        "expected_findings:\n  - {file: src/app.py, line_from: 4, line_to: 6}\n",
                        "evals/reviewer/clean.yaml": "id: clean\npr: \"#3\"\nlabel: negative\n"})
            out = repo.path(".agents/state/eval")
            for side, runs in (("baseline", {"divzero": [[fnd("src/app.py", 5)], [fnd("src/app.py", 5)]]}),
                               ("candidate", {"divzero": [[fnd("src/app.py", 30)], []]})):
                for cid, rs in dict(runs, clean=[[], []]).items():
                    os.makedirs(os.path.join(out, side, cid), exist_ok=True)
                    for i, r in enumerate(rs, 1):
                        with open(os.path.join(out, side, cid, f"{i}.json"), "w", encoding="utf-8") as f:
                            json.dump(r, f)
            rc, o = repo.run(EVAL, "score", "--out", out, "--baseline-ref", "b" * 40, "--candidate-ref", "c" * 40, "--n", "2")
            self.assertEqual(rc, 0, o)
            text = h.read(os.path.join(out, "comment.md"))
            self.assertIn("| divzero | `src/app.py:4-6` | 2/2 | 0/2 | worse: newly missed |", text)
            self.assertIn("after: missed at another line of the same file (posted `src/app.py:30`), missed (no finding)", text)
            s = json.loads(h.read(os.path.join(out, "summary.json")))
            self.assertEqual(s["wrong_place"], [{"id": "divzero", "baseline_runs": 0, "candidate_runs": 1}])
        finally:
            repo.cleanup()

    def test_eval_bundle_a_case_with_shas_runs_on_them_without_asking_the_pr(self):
        repo = h.Repo({"src/app.py": "x = 1\n"})
        try:
            c0 = repo.commit("base")
            repo.write({"src/app.py": "x = 2\n"})
            c1 = repo.commit("head")
            path = os.path.join(h.SRC, "dot-agents", "bin", "agentive-reviewer-eval")
            loader = importlib.machinery.SourceFileLoader("agentive_reviewer_eval_bin", path)
            spec = importlib.util.spec_from_loader(loader.name, loader)
            mod = importlib.util.module_from_spec(spec)
            loader.exec_module(mod)
            case = {"id": "x", "pr": "#99", "base_sha": c0, "head_sha": c1}
            self.assertEqual(mod._case_range(repo.dir, case, "o/r"), (c0, c1))               # no gh call: it would fail here
            self.assertEqual(mod._case_range(repo.dir, {"id": "y", "pr": f"{c0}..{c1}"}, "o/r"), (c0, c1))
        finally:
            repo.cleanup()


if __name__ == "__main__":
    unittest.main()
