# agentive-unit: selftest.component
# design: spec.work_loop.pr_skill, spec.work_loop.pr_explainer, spec.verification.E1a, spec.verification.E1b, contracts[pr-body-fw-review]
"""/pr body: draft (tier prediction, Explainer, Verification, Risk, agentive-review block), check, publish via gh api."""
import json
import os
import re
import sys
import unittest

import yaml

from helpers import LIB, ROOT, SRC, Sandbox, design, git, read, schema_errors, write

sys.path.insert(0, LIB)
from agentive import prbody  # noqa: E402

RULES = read(SRC, "code_review.md.regions", "rules.md")
CODE_REVIEW = f"# Review criteria\n\n<!-- AGENTIVE START rules -->\n{RULES}<!-- AGENTIVE END rules -->\n"
SPEC = "Intent: docs/plans/rate/intent.md\n\n# Spec\n\n## Requirements\n\n- R1 limit logins. Verification: test_limit\n- R2 old path (dropped)\n- R3 429 response. Verification: test_429\n\n## Design\n\nx\n"
PLAN = "# Plan\n\n## Slices\n\n- [ ] limit (R1, R3)\n\n## Risks\n\n- lockout of real users. Verified by: test_limit\n\n## Decisions\n\n- [user] 5 per minute\n"


def block(body):
    m = re.search(r"^```yaml agentive-review\n(.*?)^```$", body, re.S | re.M)
    return yaml.safe_load(m.group(1))


def fill(body):
    """What the agent does: replace every marker with text."""
    body = re.sub(r"<!-- agent: pass \| fail \| not run -->", "pass", body)
    body = re.sub(r"<!-- agent:.*?-->", "Checked by the tests above.", body)
    return re.sub(r"%% agent:.*", "a[\"start src/x.py:1\"] --> b[\"end src/x.py:2\"]", body)


class Globs(unittest.TestCase):
    def test_glob_semantics(self):
        m = prbody.glob_match
        self.assertTrue(m("**/auth/**", "src/auth/login.py"))
        self.assertTrue(m("**/auth/**", "auth/x"))
        self.assertFalse(m("**/auth/**", "src/author/x"))
        self.assertTrue(m("**/permissions*", "a/permissions.json"))
        self.assertTrue(m(".claude/settings*.json", ".claude/settings.local.json"))
        self.assertFalse(m(".claude/settings*.json", "x/.claude/settings.json"))
        self.assertTrue(m("**/.env*", ".env.example"))
        self.assertTrue(m(".agents/hooks/**", ".agents/hooks/stop-verify.py"))
        self.assertTrue(m("**/payment*/**", "svc/payments/api.go"))


class Explainer(unittest.TestCase):
    def body(self, before="```mermaid\nflowchart TD\n  a --> b\n```", after="```mermaid\nflowchart TD\n  a --> c\n```"):
        return f"## Explainer\n\ntext\n\n### Before\n\n{before}\n\n### After\n\n{after}\n\n## Verification\n\n## Risk\n\n## How to revert\n"

    def test_design_example_passes(self):
        ex = design()["view"]["explainer"]
        b = self.body("```mermaid\n" + ex["mermaid_before"] + "```", "```mermaid\n" + ex["mermaid_after"] + "```")
        self.assertEqual(prbody.explainer_problems(b), [])

    def test_problem_codes(self):
        codes = set(json.loads(read(ROOT, "design", "schemas", "pr-body-agentive-review.schema.json"))
                    ["x-agentive-body-headings"]["tier2_explainer"]["problems"])
        self.assertEqual(prbody.explainer_problems("## Verification\n"), ["no_section"])
        self.assertEqual(prbody.explainer_problems("## Verification\n\n## Explainer\n"), ["not_first"])
        self.assertIn("before_no_mermaid", prbody.explainer_problems(self.body(before="no diagram")))
        self.assertIn("after_no_mermaid", prbody.explainer_problems(self.body(after="no diagram")))
        self.assertIn("no_after", prbody.explainer_problems("## Explainer\n\n### Before\n```mermaid\n```\n"))
        swapped = "## Explainer\n\n### After\n```mermaid\n```\n### Before\n```mermaid\n```\n"
        self.assertEqual(prbody.explainer_problems(swapped), ["before_order"])
        for b in ("## Verification\n", swapped, self.body(before="x")):
            for p in prbody.explainer_problems(b):
                self.assertIn(p, codes)

    def test_heading_inside_code_fence_is_ignored(self):
        b = "```\n## Verification\n```\n" + self.body()
        self.assertEqual(prbody.explainer_problems(b), [])


class Flow(unittest.TestCase):
    def setUp(self):
        self.sb = Sandbox({"README.md": "# demo\n", "code_review.md": CODE_REVIEW, "src/app.py": "x = 1\n",
                           "docs/plans/rate/intent.md": "# Intent\n", "docs/plans/rate/spec.md": SPEC})
        self.env = self.sb.fake_gh([])  # every gh call fails: no open PR is found

    def tearDown(self):
        self.sb.close()

    def commit(self, files, msg="change"):
        for rel, text in files.items():
            write(self.sb.repo, rel, text)
        git(self.sb.repo, "add", "-A")
        git(self.sb.repo, "commit", "-q", "-m", msg)

    def verify_json(self, head=None, dirty=False):
        head = head or git(self.sb.repo, "rev-parse", "HEAD")
        doc = {"run_id": "r1", "ts": "2026-10-28T00:00:00Z", "total_failures": 0, "head_sha": head, "worktree_dirty": dirty,
               "verbs": {"lint": {"ok": True, "failures": 0, "duration_ms": 5, "cmd": "ruff check ."},
                         "test": {"ok": True, "failures": 0, "duration_ms": 9, "cmd": "pytest -q"}},
               "pending": ["typecheck"]}
        self.assertEqual(schema_errors("verify-result", doc), [])
        write(self.sb.repo, ".agents/state/verify.json", json.dumps(doc))

    def draft(self, *extra):
        p = self.sb.run(["sh", self.sb.script("pr", "pr-body.sh"), "draft", "--tool", "claude_code", "--model", "opus", *extra], env=self.env)
        return p.returncode, p.stdout

    def test_feature_pr_on_trust_boundary_needs_explainer(self):
        git(self.sb.repo, "switch", "-q", "-c", "feature/rate")
        self.commit({"docs/plans/rate/plan.md": PLAN, "src/auth/limit.py": "LIMIT = 5\n"})
        self.verify_json()
        rc, out = self.draft()
        self.assertEqual(rc, 0, out)
        self.assertIn("TIER=2 EXPLAINER=required CHAIN=rate:feature U2=not_required", out)
        self.assertIn("trust_boundary:auth:src/auth/limit.py", out)
        body = read(self.sb.repo, ".agents", "state", "pr-body.md")
        heads = [ln for ln in body.splitlines() if ln.startswith("## ")]
        self.assertEqual(heads, ["## Explainer", "## Verification", "## Risk", "## How to revert"])
        self.assertIn("- lint: `ruff check .` -> pass (exit 0), 0 failures", body)
        self.assertIn("- typecheck: not run (pending: no stack evidence yet)", body)
        self.assertIn("R1 limit logins. Verification: test_limit ->", body)
        self.assertNotIn("R2 old path", body)
        self.assertIn("lockout of real users", body)
        doc = block(body)
        self.assertEqual(doc["agentive-review"]["chain"], {"slug": "rate", "stage": "feature"})
        self.assertEqual(schema_errors("pr-body-agentive-review", doc), [])
        # the agent fills the markers; check passes only then
        check = ["sh", self.sb.script("pr", "pr-body.sh"), "check", ".agents/state/pr-body.md", "--explainer", "required"]
        self.assertEqual(self.sb.run(check).returncode, 1)
        write(self.sb.repo, ".agents/state/pr-body.md", fill(body))
        p = self.sb.run(check)
        self.assertEqual(p.returncode, 0, p.stdout)

    def test_docs_only_change_is_tier0_without_explainer(self):
        git(self.sb.repo, "switch", "-q", "-c", "docs/readme")
        self.commit({"README.md": "# demo\n\nmore words\n"})
        self.verify_json()
        rc, out = self.draft()
        self.assertEqual(rc, 0, out)
        self.assertIn("TIER=0 EXPLAINER=optional CHAIN=none", out)
        body = read(self.sb.repo, ".agents", "state", "pr-body.md")
        self.assertNotIn("## Explainer", body)
        self.assertEqual(schema_errors("pr-body-agentive-review", block(body)), [])

    def test_agents_md_and_chain_artifacts_are_not_tier0_docs(self):
        git(self.sb.repo, "switch", "-q", "-c", "intent/new-one")
        self.commit({"docs/plans/new-one/intent.md": "# Intent\n"})
        self.verify_json()
        rc, out = self.draft()
        self.assertIn("TIER=2", out)
        self.assertIn("chain_approval", out)
        self.assertIn("CHAIN=new-one:intent", out)

    def test_code_change_without_path_rule_is_uncertain(self):
        git(self.sb.repo, "switch", "-q", "-c", "fix/app")
        self.commit({"src/app.py": "x = 2\n"})
        self.verify_json()
        rc, out = self.draft()
        self.assertIn("TIER=uncertain EXPLAINER=required", out)

    def test_confirm_scope_hit_without_confirmation(self):
        git(self.sb.repo, "switch", "-q", "-c", "ci/change")
        self.commit({".github/workflows/ci.yml": "on: push\n"})
        self.verify_json()
        rc, out = self.draft()
        self.assertIn("U2=not_obtained", out)
        self.assertIn("U2.non_interactive", out)
        doc = block(read(self.sb.repo, ".agents", "state", "pr-body.md"))
        self.assertEqual(doc["agentive-review"]["u2"], {"run": "interactive", "human_confirmation": "not_obtained",
                                                        "confirm_scope_hits": [".github/workflows/ci.yml"]})
        self.assertEqual(schema_errors("pr-body-agentive-review", doc), [])

    def test_confirmed_hit_from_precommit_record(self):
        git(self.sb.repo, "switch", "-q", "-c", "ci/ok")
        self.commit({".github/workflows/ci.yml": "on: push\n"})
        self.verify_json()
        rec = {"blocking": [], "nits": [], "at": "2026-10-28T00:00:00Z",
               "confirm_scope_hits": [{"path": ".github/workflows/ci.yml", "category": "ci", "glob": ".github/workflows/**"}],
               "u2": {"run": "interactive", "human_confirmation": "obtained", "action": "commit"},
               "reviewer": {"reviewer_id": "correctness-reviewer", "session_id": "rev-1", "context": "fresh"}}
        self.assertEqual(schema_errors("precommit-review", rec), [])
        write(self.sb.repo, ".agents/state/precommit-review.json", json.dumps(rec))
        rc, out = self.draft()
        self.assertIn("U2=obtained", out)
        doc = block(read(self.sb.repo, ".agents", "state", "pr-body.md"))
        self.assertEqual(doc["agentive-review"]["reviews"][0]["source"], "precommit-review")
        self.assertEqual(doc["agentive-review"]["reviews"][0]["session_id"], "rev-1")
        self.assertEqual(schema_errors("pr-body-agentive-review", doc), [])

    def test_headless_run_never_records_obtained(self):
        git(self.sb.repo, "switch", "-q", "-c", "ci/headless")
        self.commit({".github/workflows/ci.yml": "on: push\n"})
        self.verify_json()
        self.env["AGENTIVE_HEADLESS"] = "1"
        rc, out = self.draft()
        doc = block(read(self.sb.repo, ".agents", "state", "pr-body.md"))
        self.assertEqual(doc["agentive-review"]["u2"]["run"], "non_interactive")
        self.assertEqual(doc["agentive-review"]["u2"]["human_confirmation"], "not_obtained")

    def test_intent_skipped_line(self):
        git(self.sb.repo, "switch", "-q", "-c", "fix/drop")
        self.commit({"src/app.py": "x = 3\n"})
        self.verify_json()
        self.draft("--intent-skipped")
        self.assertIn("\nintent 생략: 사용자 지시\n", read(self.sb.repo, ".agents", "state", "pr-body.md"))

    def test_stale_or_dirty_verify_is_not_reused(self):
        git(self.sb.repo, "switch", "-q", "-c", "fix/stale")
        self.commit({"src/app.py": "x = 4\n"})
        self.verify_json(head="0" * 40)
        rc, out = self.draft()
        self.assertEqual(rc, 1)
        self.assertIn("reason=verify_stale", out)
        self.verify_json(dirty=True)
        self.assertIn("reason=verify_stale", self.draft()[1])
        self.verify_json()
        write(self.sb.repo, "src/app.py", "x = 5\n")
        self.assertIn("reason=verify_dirty", self.draft()[1])

    def test_default_branch_is_refused(self):
        rc, out = self.draft()
        self.assertEqual(rc, 1)
        self.assertIn("reason=on_default_branch", out)

    def test_open_pr_draft_holds_only_verification(self):
        git(self.sb.repo, "switch", "-q", "-c", "fix/refresh")
        self.commit({"src/app.py": "x = 6\n"})
        self.verify_json()
        self.env = self.sb.fake_gh([(r"pulls\?head=", {"stdout": [{"number": 9}]})])
        rc, out = self.draft()
        self.assertIn("MODE=refresh PR=9", out)
        body = read(self.sb.repo, ".agents", "state", "pr-body.md")
        self.assertEqual([ln for ln in body.splitlines() if ln.startswith("## ")], ["## Verification"])
        write(self.sb.repo, ".agents/state/pr-body.md", fill(body))
        p = self.sb.run(["sh", self.sb.script("pr", "pr-body.sh"), "check", ".agents/state/pr-body.md"])
        self.assertEqual(p.returncode, 0, p.stdout)

    def test_explain_draft(self):
        git(self.sb.repo, "switch", "-q", "-c", "fix/explain")
        self.commit({"src/auth/x.py": "y = 1\n"})
        rc, out = self.draft("--explain")
        self.assertEqual(rc, 0, out)
        body = read(self.sb.repo, ".agents", "state", "pr-explainer.md")
        self.assertTrue(body.startswith("## Explainer"))
        self.assertEqual(prbody.explainer_problems(body), [])


class Publish(unittest.TestCase):
    def setUp(self):
        self.sb = Sandbox({"README.md": "# demo\n", "code_review.md": CODE_REVIEW})
        git(self.sb.repo, "switch", "-q", "-c", "docs/x")
        write(self.sb.repo, "README.md", "# demo\n\nmore\n")
        git(self.sb.repo, "commit", "-q", "-am", "docs")
        git(self.sb.repo, "push", "-q", "-u", "origin", "docs/x")
        self.full = ("## Verification\n\nHEAD: abc\n- lint: ok\n\n## Risk\n\nnone\n\n## How to revert\n\nrevert\n\n"
                     "```yaml agentive-review\nagentive-review:\n  implementer:\n    tool: claude_code\n    model: m\n"
                     "    session_id: s\n    started_at: \"2026-10-28T00:00:00Z\"\n  reviews: []\n```\n")
        write(self.sb.repo, ".agents/state/body.md", self.full)

    def tearDown(self):
        self.sb.close()

    def publish(self, env, *extra):
        return self.sb.run(["sh", self.sb.script("pr", "pr-body.sh"), "publish", ".agents/state/body.md", *extra], env=env)

    def test_creates_the_pr_through_gh_api(self):
        env = self.sb.fake_gh([(r"pulls\?head=", {"stdout": []}),
                               (r"-X POST .*pulls", {"stdout": {"number": 12, "html_url": "https://x/12"}})])
        p = self.publish(env, "--title", "docs: readme")
        self.assertEqual(p.returncode, 0, p.stdout)
        self.assertIn("PR=12 URL=https://x/12 CREATED", p.stdout)
        post = [c for c in self.sb.gh_calls() if "POST" in c["args"]][0]
        self.assertIn("base=main", post["args"])
        self.assertIn("head=docs/x", post["args"])
        self.assertEqual(post["body"], self.full)

    def test_open_pr_gets_only_verification_replaced(self):
        old = ("## Explainer\n\nE\n\n### Before\n```mermaid\nflowchart TD\n```\n### After\n```mermaid\nflowchart TD\n```\n\n"
               "## Verification\n\nold\n\n## Risk\n\nkept risk\n\n## How to revert\n\nkept\n\n```yaml agentive-review\nagentive-review:\n  pipeline: kept\n```\n")
        env = self.sb.fake_gh([(r"pulls\?head=", {"stdout": [{"number": 7}]}),
                               (r"-X PATCH", {"stdout": {"number": 7}}),
                               (r"pulls/7$", {"stdout": {"number": 7, "body": old}})])
        p = self.publish(env)
        self.assertEqual(p.returncode, 0, p.stdout)
        self.assertIn("next: /pr-feedback 7", p.stdout)
        new = [c for c in self.sb.gh_calls() if "PATCH" in c["args"]][0]["body"]
        self.assertIn("HEAD: abc", new)
        self.assertNotIn("\nold\n", new)
        for kept in ("## Explainer", "kept risk", "pipeline: kept", "### Before"):
            self.assertIn(kept, new)

    def test_branch_must_be_pushed(self):
        git(self.sb.repo, "switch", "-q", "-c", "docs/unpushed")
        env = self.sb.fake_gh([])
        p = self.publish(env, "--title", "t")
        self.assertEqual(p.returncode, 1)
        self.assertIn("NEXT=git push -u origin docs/unpushed", p.stdout)


class Splice(unittest.TestCase):
    def test_insert_explainer_first(self):
        out = prbody.splice("## Verification\n\nv\n", "## Explainer", "## Explainer\n\nnew")
        self.assertTrue(out.startswith("## Explainer\n\nnew\n\n## Verification"))


if __name__ == "__main__":
    unittest.main()
