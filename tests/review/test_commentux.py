# agentive-unit: selftest.component
# design: decisions.accepted[COMMENTUX], decisions.accepted[EFFECTIVECHECKS], spec.review_pipeline.reviewer_eval.ci, spec.review_pipeline.stages[RV1], spec.review_pipeline.stages[RV2], docs/impl/decisions-log.md §23
"""COMMENTUX (user 2026-11-01, "가독성이 하나도 없어서 ... / 추천대로 해") and E2E-34 / E2E-35 (decision e2e_32_36): the summary comment,
the reviewer-eval comment and the gate step summary open with the verdict and what to do, give grouped plain reasons and fold the
rest; the install PR shows typecheck as not run from the PR's own manifest (display only); env-var reasons are one line.

The PR #1 fixtures rebuild the end-to-end install PR of 2026-10-31 (135 files, 30 environment variables, 47 reviewer changes) and
check the rendered comments: the visible part (before the first <details>) is about 15 lines."""
import json
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import helpers as h  # noqa: E402
import test_stages as ts  # noqa: E402
from test_e2e_batch_d import summary  # noqa: E402
from test_pipeline import REVIEW, PipelineBase, body  # noqa: E402

from agentive import ayaml  # noqa: E402
from agentive.review import config as cfg, evals, post, rv1, rv2, util  # noqa: E402

WARN, OKI, BLOCK, INFO = "⚠️", "✅", "⛔", "ℹ️"
ENV30 = ["AGENTIVE_ACTION_SESSION_ID", "AGENTIVE_CI_API_KEY", "AGENTIVE_CI_OAUTH_TOKEN", "AGENTIVE_CLAUDE", "AGENTIVE_CODEX",
         "AGENTIVE_DEFAULT_BRANCH", "AGENTIVE_EVENT_ACTION", "AGENTIVE_EVENT_LABEL", "AGENTIVE_HEADLESS", "AGENTIVE_HEAD_REPO",
         "AGENTIVE_LLM_CONCLUSION", "AGENTIVE_LLM_EXECUTION_FILE", "AGENTIVE_LLM_OUTCOME", "AGENTIVE_SESSION_ID", "AGENTIVE_TOOL",
         "ANTHROPIC_API_KEY", "CI", "CLAUDECODE", "CLAUDE_CODE_OAUTH_TOKEN", "CODEX_API_KEY", "GH_REPO", "GITHUB_ACTIONS",
         "GITHUB_EVENT_NAME", "GITHUB_OUTPUT", "GITHUB_REPOSITORY", "GITHUB_RUN_ATTEMPT", "GITHUB_RUN_ID", "GITHUB_SERVER_URL",
         "GITHUB_SHA", "GITHUB_STEP_SUMMARY"]
CODES = ("trust_boundary", "always_human", "U2.non_interactive", "tier2_class", "large_change", "ai_review_skipped", "tier2",
         "coverage_unavailable", "protected_check_config")
SKIP = {"kind": "workflow_change", "exempt": True, "next": "x",
        "detail": "workflow-change PR: AI review skipped by the action; human review. The Claude Code action runs only when the workflow "
                  "file of the run is identical to the default branch's copy (its workflow validation), and this PR adds (first install: "
                  "the default branch has no copy yet) .github/workflows/agentive-review.yml; a human reviews and merges it, and later "
                  "PRs get the AI review once it is merged"}


def pr1_report():
    """The PR #1 install report as the gate wrote it (RV2 hits and confirm-scope categories with the counts of the PR)."""
    hits = [{"path": f".agents/lib/agentive/m{i}.py", "glob": ".agents/**", "reason": "check_config", "layer": "protected"} for i in range(112)]
    hits += [{"path": f".codex/agents/a{i}.toml", "glob": ".codex/**", "reason": "agent_hooks_settings", "layer": "floor"} for i in range(11)]
    hits += [{"path": ".claude/settings.json", "glob": ".claude/settings*.json", "reason": "agent_hooks_settings", "layer": "floor"}]
    hits += [{"path": f".github/workflows/{n}", "glob": ".github/workflows/**", "reason": "ci_config", "layer": "floor"}
             for n in ("agentive-lint.yml", "agentive-review.yml", "reviewer-eval.yml")]
    cs = ([{"path": f"c{i}", "category": "codex_dir", "glob": ".codex/**"} for i in range(11)]
          + [{"path": f"h{i}", "category": "hooks", "glob": ".agents/hooks/**"} for i in range(10)]
          + [{"path": f"w{i}", "category": "ci", "glob": ".github/workflows/**"} for i in range(3)])
    rv4 = ["trust_boundary", "always_human", "always_human: " + rv2.env_vars_text(ENV30), "always_human: gate or agent config change "
           "(protected_check_config)", "always_human: more than 100 changed files (135)", "always_human: agentive-check: tag added or removed",
           "U2.non_interactive", "tier2_class", "large_change"]
    rv8 = {"decision": "human_required", "check_conclusion": "success", "auto_merge": False, "auto_merge_setting": "low_risk",
           "reasons": ["tier2", "U2.non_interactive", "always_human", "trust_boundary", "coverage_unavailable",
                       "coverage_unavailable: new-code coverage skipped (no coverage report)", "ai_review_skipped",
                       "ai_review_skipped: workflow_change: " + SKIP["detail"]],
           "reason_codes": ["tier2", "U2.non_interactive", "always_human", "trust_boundary", "coverage_unavailable", "ai_review_skipped"],
           "required_checks": {"required": ["lint", "typecheck", "test"], "not_required": [], "source": "framework default",
                               "display": {"required": ["lint", "test"], "not_required": ["typecheck"], "source": cfg.FIRST_INSTALL_SOURCE}}}
    return {"head_sha": "600c53709e885379244f10545bf9b7aec8cfc21f",
            "rv1": {"checks": [{"name": "lint", "status": "pass"}, {"name": "typecheck", "status": "skipped"}, {"name": "test", "status": "pass"}],
                    "diff_stats": {"files": 135, "added": 15435, "deleted": 0}, "signals": {"env_vars_changed": ENV30}},
            "rv2": {"hits": hits, "confirm_scope_hits": cs}, "rv3": {"classes": ["docs", "security", "config", "other"], "method": "llm"},
            "rv4": {"tier": 2, "reasons": rv4}, "rv7b": {"score": None, "reason": "not_tier1"},
            "review_round": {"round": 1, "mode": "full"}, "findings": [], "rv8": rv8}


def pr1_reviewer_reasons():
    out = [".agents/bin/agentive-review (.agents/bin/agentive-review)"]
    out += [f".agents/lib/agentive/review/{n}.py (.agents/lib/agentive/review/**)" for n in (
        "__init__", "attest", "config", "evals", "findings", "github", "gitio", "independence", "killswitch", "llmstate", "pipeline",
        "post", "pr_body", "rv1", "rv2", "rv3", "rv4", "rv7b", "rv8", "secpaths", "settings", "tasks", "util")]
    names = ("classifier", "coordinator", "correctness-reviewer", "raise-only", "security-reviewer", "testing-reviewer", "verifier")
    out += [f".agents/agents/{n}.md (.agents/agents/**)" for n in names]
    out += [f".claude/agents/{n}.md (.claude/agents/**)" for n in names]
    out += [f".codex/agents/{n}.toml (.codex/agents/**)" for n in names]
    return out + ["code_review.md (code_review.md)", ".github/workflows/agentive-review.yml: ai-review job changed"]


class CommentUXTest(unittest.TestCase):
    decide = ts.GateTest.decide

    def test_commentux_pr1_summary_comment(self):
        text = post.summary_comment(pr1_report(), 0, {"ai_review_skipped": SKIP})
        lines = text.splitlines()
        self.assertEqual(lines[0], post.SUMMARY_MARKER)                                  # the upsert marker stays the first line
        self.assertEqual(lines[1], f"### {WARN} agentive review: needs a human review — install PR")
        visible = h.visible_lines(text)
        self.assertLessEqual(len(visible), 15, "\n".join(visible))                      # was 3,465 characters of codes
        self.assertLessEqual(len("\n".join(visible)), 1300)
        vis = text.split("<details>")[0]
        self.assertIn("**What to do:** Review this install PR and merge it by hand.", vis)
        why = [ln for ln in vis.split("**Why**\n")[1].split("\n\n")[0].splitlines()]
        self.assertLessEqual(len(why), 5)
        self.assertIn("- The code reads 30 environment variables it did not read before, or stopped reading them (names in Details).", why)
        self.assertNotIn("CI workflow environment", text)                                 # the signal is code lookups, not workflow env:
        self.assertIn("- It changes security-sensitive files (127 of 135 files: review/gate config 112, agent hooks and settings 12, "
                      "CI workflows 3); a file is classified as a security change.", why)
        self.assertIn("**Checks:** lint passed · test passed · typecheck not run (no command in this repo)", vis)   # E2E-34
        self.assertNotIn("typecheck (skipped)", vis)
        self.assertIn("**AI findings:** not run (the AI review was skipped)", vis)
        for code in CODES:                                                                  # R5: codes only inside Details
            self.assertNotIn(code, vis, code)
        details = text.split("<details>")[1]
        for name in ENV30:
            self.assertIn(f"`{name}`", details)
        self.assertIn("- Reason codes: ", details)
        self.assertIn("- Auto-merge setting `low_risk` (framework default)", details)
        self.assertIn("Shown for information: the gate read the base branch", details)
        self.assertNotIn("> [!", text)                                                      # no alert blocks

    def test_commentux_pr1_reviewer_eval_comment(self):
        s = evals.summarize([], {}, {}, 3, "b83665e43f22a0bbd1ad116e620d5c8ad9f03342", "600c53709e885379244f10545bf9b7aec8cfc21f",
                            no_baseline=True)
        text = evals.comment(s, pr1_reviewer_reasons())
        lines = text.splitlines()
        self.assertEqual(lines[0], evals.MARKER)
        self.assertEqual(lines[1], f"### {INFO} agentive reviewer eval: not compared — first install")
        visible = h.visible_lines(text)
        self.assertLessEqual(len(visible), 15, "\n".join(visible))                       # was 3,825 characters
        self.assertTrue(all(len(ln) < 400 for ln in visible), visible)                     # the 2,941-character path line is folded
        self.assertIn("This PR changes the AI reviewer (47 changes: review program 24, agents 7, Claude Code agents 7, Codex agents 7, "
                      "review rules 1, ai-review workflow job 1).", text)
        self.assertIn("**What to do:** Nothing for this check.", text)
        self.assertEqual(ayaml.loads(util.fenced_blocks(text, "yaml agentive-reviewer-eval")[0]), s)   # still machine-read
        self.assertIn("<summary>Machine-read result (CI writes it; do not edit)</summary>", text)
        self.assertIn("`.agents/lib/agentive/review/`: `__init__.py`, `attest.py`", text)

    def test_commentux_reviewer_eval_real_comparison_shows_the_table(self):
        pos = {"id": "p1", "label": "positive"}
        neg = {"id": "n1", "label": "negative"}
        hit = [{"file": "a.py", "line": 10, "instances": [], "blocking": True}]
        s = evals.summarize([dict(pos, expected_findings=[{"file": "a.py", "line_from": 10, "line_to": 12}]), neg],
                            {"p1": [[]] * 3, "n1": [[]] * 3}, {"p1": [hit] * 3, "n1": [[]] * 3}, 3, "b" * 40, "c" * 40)
        self.assertEqual(s["reason"], "improved")
        text = evals.comment(s, ["code_review.md (code_review.md)"])
        self.assertIn(f"### {OKI} agentive reviewer eval: improved — merge as usual", text)
        self.assertIn("| known bugs caught (mean of 3 runs, of 1 cases) | 0.0 (spread 0) | 1.0 |", text)
        self.assertIn("<details><summary>Per case (2): newly missed 0, newly caught 1, newly noisy 0, 1 unchanged</summary>", text)   # EVALBUNDLE

    def test_commentux_verdict_table(self):
        auto = self.decide()
        self.assertTrue(auto["auto_merge"])
        t = post.summary_comment(summary(auto), 0, {})
        self.assertIn(f"### {OKI} agentive review: merges automatically", t)
        self.assertIn("**What to do:** Nothing to do. GitHub merges this PR once its merge requirements are met.", t)
        self.assertNotIn("**Why**", t)
        shadow = self.decide(tier=1, rv4_reasons=["tier1"], rv7b={"score": 3, "reason": "computed", "human_if_score_gt": 28})
        self.assertIn("would_auto_merge", shadow["reason_codes"])
        t = post.summary_comment(summary(shadow, rv4={"tier": 1, "reasons": ["tier1"]}), 0, {})
        self.assertIn(f"### {OKI} agentive review: ready for a human to merge", t)
        self.assertIn(f"`{cfg.AUTO_MERGE_LABEL}`", t.split("<details>")[0])
        fnd = [{"id": "F-3c9a1e0b2d", "severity": "important", "blocking": True, "file": "src/auth/session.py", "line": 88,
                "summary": "s", "verification": {"verdict": "confirmed"}}]
        blocked = self.decide(findings=fnd)
        self.assertEqual(blocked["decision"], "block")
        rep = dict(summary(blocked), findings=fnd)
        t = post.summary_comment(rep, 0, {})
        self.assertIn(f"### {BLOCK} agentive review: blocked — 1 AI finding needs an answer", t)
        self.assertIn("**What to do:** Author: fix finding F-3c9a1e0b2d or reply in its thread with an agentive-reply line, then push.", t)
        self.assertIn("| F-3c9a1e0b2d | `src/auth/session.py:88` | confirmed | open |", t)
        self.assertIn("**AI findings:** 1 important, 1 blocking (table below)", t)
        nox = self.decide(tier=2, rv4_reasons=["tier2", "trust_boundary"], explainer=({}, ["no_section"]))
        t = post.summary_comment(summary(nox, rv4={"tier": 2, "reasons": ["trust_boundary"]}), 0, {})
        self.assertIn(f"### {BLOCK} agentive review: blocked — PR body needs an Explainer", t)
        self.assertIn("- The PR body needs a ## Explainer section (missing).", t)
        # every heading icon is followed by a word of the same meaning (R9)
        for icon, word in ((OKI, "merges automatically"), (WARN, "needs a human"), (BLOCK, "blocked")):
            self.assertTrue(any(f"{icon} agentive review: {word}" in x for x in (
                post.summary_comment(summary(auto), 0, {}), post.summary_comment(pr1_report(), 0, {"ai_review_skipped": SKIP}),
                post.summary_comment(rep, 0, {}))), icon)

    def test_commentux_ai_step_failure_and_dependency_verdicts(self):
        r = self.decide(tier=1, rv4_reasons=["tier1"], llm_problems=["rv5-correctness-reviewer: no output"])
        text = post.summary_comment(summary(r, rv4={"tier": 1, "reasons": ["tier1"]}), 0, {
            "ai_step_failed": "AI review step failed (ai_step_failed:auth): the CI credential was rejected; check the repository secret "
                              "(CLAUDE_CODE_OAUTH_TOKEN or ANTHROPIC_API_KEY), then re-run the ai-review job (run https://x/runs/9)"})
        vis = text.split("<details>")[0]
        self.assertIn(f"### {WARN} agentive review: needs a human review — AI review failed", vis)
        self.assertIn("**What to do:** Maintainer: check the repository secret (CLAUDE_CODE_OAUTH_TOKEN or ANTHROPIC_API_KEY), then "
                      "re-run the ai-review job ([run](https://x/runs/9)). Until it passes, a human reviews this PR.", vis)
        self.assertIn("- The AI review step failed: the CI credential was rejected.", vis)
        self.assertIn("**AI findings:** not run (the AI review step failed)", vis)
        dc = {"added": ["pypi:httpx"], "removed": [], "changed": [], "lockfiles": ["uv.lock"], "unreadable": []}
        reasons = ["dependency_change", "dependency_change: " + rv1.dependency_change_text(dc, "pass")]
        r = self.decide(tier=2, rv4_reasons=reasons, explainer=({"section": True}, []))
        vis = post.summary_comment(summary(r, rv4={"tier": 2, "reasons": reasons}), 0, {}).split("<details>")[0]
        self.assertIn(f"### {WARN} agentive review: needs a human review — dependency change", vis)
        self.assertIn("- Dependencies changed: added pypi:httpx; lockfile uv.lock.", vis)
        self.assertIn("- Dependency review ran and found no critical CVE (information only).", vis)
        self.assertIn("**What to do:** Check the dependency change: why each package is needed", vis)

    def test_commentux_why_keeps_five_lines(self):
        items = [{"group": "G12", "text": f"reason {i}.", "codes": ["x"], "block": False} for i in range(8)]
        why, rest = post._why_block(items)
        self.assertEqual(len(why), 5)
        self.assertEqual(why[-1], "- + 4 more in Details")
        self.assertEqual(len(rest), 4)
        rep = pr1_report()
        text = post.summary_comment(rep, 0, {"ai_review_skipped": SKIP})
        self.assertIn("- + 2 more in Details", text)
        self.assertIn("- Also: It is large: 135 files, 15,435 changed lines.", text)

    def test_commentux_gate_step_summary_is_markdown_and_the_log_line_is_unchanged(self):
        md = post.gate_summary(pr1_report(), {"ai_review_skipped": SKIP})
        self.assertTrue(md.startswith(f"### {WARN} agentive-review-gate: needs a human review — install PR (tier 2)\n"), md[:200])
        self.assertIn("**Checks:** lint passed · test passed · typecheck not run (no command in this repo)", md)
        self.assertIn("<details><summary>Reason codes and auto-merge setting</summary>", md)
        self.assertLessEqual(len(h.visible_lines(md)), 15)


class GateStepSummaryTest(PipelineBase):
    def test_commentux_gate_writes_markdown_to_the_step_summary(self):
        self.repo.write({"src/app.py": "import os\n\n\ndef run():\n    return 2\n"})
        head = self.repo.commit("code")
        work = self.repo.path(".agents/state/wg")
        os.makedirs(work, exist_ok=True)
        summ = self.repo.path(".agents/state/step-summary.md")
        rc, out = self.repo.run(REVIEW, "gate", "7", "--pr-json", self.pr(head, body(explainer=True)), "--work", work,
                                "--comments-json", self.comments([]), env={"GITHUB_STEP_SUMMARY": summ})
        self.assertIn(rc, (0, 1), out)
        md = h.read(summ)
        self.assertTrue(md.startswith("### "), md[:200])
        self.assertIn("agentive-review-gate: ", md.splitlines()[0])
        self.assertIn("**What to do:** ", md)
        log = out.splitlines()
        self.assertTrue(any(ln.startswith("agentive-review-gate: ") for ln in log), out)      # plain log lines stay
        self.assertTrue(log[-1].startswith("AGENTIVE_GATE="), out)                              # prstate reads this line
        self.assertRegex(log[-1], r"^AGENTIVE_GATE=(\S+) .*?reasons=([^;\s]*)")


MANIFEST = ("version: 1\nverbs:\n  lint:\n    cmd: ruff check .\n  typecheck:\n    status: pending\n  test:\n    cmd: pytest\n")


class E2E34FirstInstallDisplayTest(PipelineBase):
    def gate(self, head, work):
        work = self.repo.path(f".agents/state/{work}")
        rc, out = self.repo.run(REVIEW, "gate", "7", "--pr-json", self.pr(head, body(explainer=True)), "--work", work,
                                "--comments-json", self.comments([]))
        return json.loads(h.read(os.path.join(work, "review-report.json"))), out

    def test_e2e_34_first_install_shows_the_heads_pending_verbs_and_decides_from_the_base(self):
        self.repo.write({".agents/manifest.yaml": MANIFEST})
        head = self.repo.commit("install")
        rep, out = self.gate(head, "w34")
        self.assertEqual(self.rep_schema(rep), [])
        rc = rep["rv8"]["required_checks"]
        self.assertEqual((rc["required"], rc["not_required"], rc["source"]), (["lint", "typecheck", "test"], [], "framework default"))
        self.assertEqual(rc["display"], {"required": ["lint", "test"], "not_required": ["typecheck"], "source": cfg.FIRST_INSTALL_SOURCE})
        text = post.summary_comment(rep, 0, {})
        self.assertIn("typecheck not run (no command in this repo)", text.split("<details>")[0])
        self.assertIn("- not required: typecheck (no command in this repo; info, not blocking)", out)       # gate log, display too
        # the decision is the one without the display: the same reasons as a gate run without the head manifest
        self.repo.write({".agents/manifest.yaml": None, "src/app.py": "import os\n\n\ndef run():\n    return 3\n"})
        h.git(self.repo.dir, "checkout", "-q", "-b", "no-manifest", self.base)
        self.repo.write({"src/app.py": "import os\n\n\ndef run():\n    return 3\n"})
        plain, _ = self.gate(self.repo.commit("code"), "w34b")
        self.assertNotIn("display", plain["rv8"]["required_checks"])
        self.assertEqual(plain["rv8"]["required_checks"]["required"], rc["required"])

    def test_e2e_34_a_base_with_a_manifest_gets_no_display(self):
        h.git(self.repo.dir, "checkout", "-q", "main")
        self.repo.write({".agents/manifest.yaml": MANIFEST})
        self.base = self.repo.commit("manifest on main")
        h.git(self.repo.dir, "checkout", "-q", "-b", "feat2")
        self.repo.write({"src/app.py": "import os\n\n\ndef run():\n    return 4\n"})
        rep, _ = self.gate(self.repo.commit("code"), "w34c")
        self.assertNotIn("display", rep["rv8"]["required_checks"])
        self.assertEqual(rep["rv8"]["required_checks"]["not_required"], ["typecheck"])


class E2E38WordingTest(unittest.TestCase):
    """E2E-38 (wording nits of the E2E run of 2026-11-01): no repeated count, no bare internal term, no repeated `Reviewer:`."""

    def report(self, n_files, n_hits):
        rep = pr1_report()
        rep["rv2"]["hits"] = [{"path": f".agents/lib/agentive/m{i}.py", "glob": ".agents/**", "reason": "check_config", "layer": "protected"}
                              for i in range(n_hits)]
        rep["rv1"]["diff_stats"] = {"files": n_files, "added": 1000, "deleted": 183}
        return rep

    def why(self, rep):
        text = post.summary_comment(rep, 0, {"ai_review_skipped": SKIP})
        vis = text.split("<details>")[0]
        return text, vis, [ln for ln in vis.split("**Why**\n")[1].split("\n\n")[0].splitlines()]

    def test_e2e_38_all_files_of_one_kind_say_it_once(self):
        text, vis, why = self.why(self.report(22, 22))
        line = next(ln for ln in why if "security-sensitive" in ln)
        self.assertEqual(line, "- It changes security-sensitive files: all 22 files are review/gate config; a file is classified as a security change.")
        self.assertNotIn("22 of 22", text)
        self.assertNotIn("config 22", vis)                                               # Details keeps `reason count`
        self.assertLessEqual(len(h.visible_lines(text)), 15)
        # a part of the files, one kind: the kind without its count; several kinds keep `name count`
        _, _, why = self.why(self.report(40, 22))
        self.assertIn("- It changes security-sensitive files (22 of 40 files: review/gate config); a file is classified as a security change.", why)
        self.assertEqual(self.why(pr1_report())[2][1].count("112"), 1)                 # the PR #1 line keeps its counts

    def test_e2e_38_internal_term_is_explained_and_codes_stay_in_details(self):
        text = post.summary_comment(pr1_report(), 0, {"ai_review_skipped": SKIP})
        vis, details = text.split("<details>")[0], text.split("<details>", 1)[1]
        self.assertNotIn("an `agentive-check:` tag", text)
        self.assertIn("It adds or removes a tag that ties a written rule to the check enforcing it (a line starting `agentive-check:`).",
                      text)
        self.assertIn("`agentive-check:`", details)                                      # the tag name stays in Details
        self.assertNotIn("agentive-check", vis)                                          # the first screen has no bare internal term

    def test_e2e_38_reviewer_prefix_is_not_repeated(self):
        for rep, extras in ((pr1_report(), {"ai_review_skipped": SKIP}), (pr1_report(), {"reviewer_eval": "not scored (x)"})):
            vis = post.summary_comment(rep, 0, extras).split("<details>")[0]
            self.assertEqual(vis.count("Reviewer:"), 0, vis)
        s = evals.summarize([], {}, {}, 3, "b" * 40, "c" * 40)
        text = evals.comment(s, ["code_review.md (code_review.md)"])
        self.assertNotIn("Reviewer:", text.split("<details>")[0])
        self.assertIn("**What to do:** Review this reviewer change by hand.", text)
        for reason in ("newly_missed", "no_gain"):
            todo = evals._result(dict(s, reason=reason, newly_missed=["a"], newly_noisy=["b"]))[1]
            self.assertNotIn("Reviewer:", todo)
            self.assertIn("Or, to merge anyway, add `eval-override: <reason>`", todo)

    def test_e2e_38_one_group_is_not_counted_twice_and_the_eval_line_is_short(self):
        s = evals.summarize([], {}, {}, 3, "b" * 40, "c" * 40)
        reasons = [f".agents/lib/agentive/review/m{i}.py (.agents/lib/agentive/review/**)" for i in range(7)]
        text = evals.comment(s, reasons)
        self.assertIn("This PR changes the AI reviewer (7 changes: review program).", text)
        self.assertIn("<summary>Reviewer files changed (7): review program</summary>", text)
        self.assertNotIn("review program 7", text)
        self.assertIn("- review program (7): ", text)                                    # the list line keeps its count
        two = evals.comment(s, reasons + ["code_review.md (code_review.md)"])
        self.assertIn("(8 changes: review program 7, review rules 1)", two)


class E2E35EnvVarTest(unittest.TestCase):
    def test_e2e_35_one_reason_for_all_env_vars(self):
        names = [f"V{i}" for i in range(7)]
        self.assertEqual(rv2.env_vars_text(names), "env vars added or removed (7): V0, V1, V2, V3, V4 (+2 more)")
        self.assertEqual(rv2.env_vars_text(["A"]), "env vars added or removed (1): A")

    def test_e2e_35_agentive_owned_paths_are_not_read_and_the_rule_stays(self):
        repo = h.Repo({"src/app.py": "def run():\n    return 1\n"}, install=False)
        self.addCleanup(repo.cleanup)
        base = repo.commit("base")
        repo.write({".agents/lib/x.py": "import os\nA = os.environ.get('AGENTIVE_ONLY')\n",
                    ".claude/hooks/y.py": "import os\nB = os.environ.get('CLAUDE_ONLY')\n",
                    "src/app.py": "import os\n" + "".join(f"V{i} = os.environ.get('APP_V{i}')\n" for i in range(7)) + "def run():\n    return 1\n"})
        head = repo.commit("env")
        from agentive.review import gitio
        r = rv1.run(gitio.Diff(repo.dir, base, head), {}, cfg.DEFAULT_CONFIG, [], [], [])
        env = r["signals"]["env_vars_changed"]
        self.assertEqual(env, [f"APP_V{i}" for i in range(7)])
        _, always = rv2.run(gitio.Diff(repo.dir, base, head), r["_statuses"], r["_hunks"], cfg.load_rules({}.get, "code_review.md").value,
                            {}, r["signals"], None, [])
        env_items = [d for c, d in always if c == "always_human" and d.startswith("env var")]
        self.assertEqual(env_items, ["env vars added or removed (7): APP_V0, APP_V1, APP_V2, APP_V3, APP_V4 (+2 more)"])


if __name__ == "__main__":
    unittest.main()
