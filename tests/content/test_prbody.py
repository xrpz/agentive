# agentive-unit: selftest.component
# design: spec.work_loop.pr_skill, spec.work_loop.pr_explainer, spec.verification.E1a, spec.verification.E1b, contracts[pr-body-fw-review], decisions.accepted[OPENMARK]
"""/pr body: draft (tier prediction, Explainer, Verification, Risk, agentive-review block), check, publish via gh api."""
import json
import os
import re
import sys
import unittest

import yaml

from helpers import LIB, ROOT, SRC, DepthSandbox, Sandbox, design, git, read, schema_errors, sh, write

sys.path.insert(0, LIB)
from agentive import prbody  # noqa: E402

RULES = re.sub(r"(?m)^[ \t]*%%agentive:render\.rules_\w+%%\n", "", read(SRC, "code_review.md.regions", "rules.md"))   # engine placeholders (SECPATHS)
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
    return re.sub(r"%% agent:.*", FLOW.replace("\\", "\\\\"), body)


# a diagram that follows the Explainer visual profile (work_loop.pr_explainer.visual), used for Before and After alike
FLOW = "\n  ".join(['a(["Start the app"])', 'b["Load the config<br/>load_config()"]', 'c(["Config ready"])', "a --> b", "b --> c"])


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


EXAMPLE = read(SRC, "dot-agents", "skills", "pr", "templates", "explainer-example.md")
STYLE_CODES = json.loads(read(ROOT, "design", "schemas", "pr-body-agentive-review.schema.json"))[
    "x-agentive-body-headings"]["tier2_explainer"]["local_style_check"]["problems"]


class ExplainerVisual(unittest.TestCase):
    """Decision explainer_visual (user 2026-10-31): the Explainer diagram profile and its local-only checks."""

    def body(self, explainer=EXAMPLE):
        return explainer.rstrip("\n") + "\n\n## Verification\n\n## Risk\n\n## How to revert\n"

    def codes(self, explainer):
        b = self.body(explainer)
        self.assertEqual(prbody.explainer_problems(b), [])          # presence holds: only the profile is under test
        probs = prbody.explainer_style_problems(b)
        for code, msg in probs:
            self.assertIn(code, STYLE_CODES)
            self.assertNotIn("\n", msg)
            self.assertLess(len(msg), 400, msg)
        return [c for c, _ in probs]

    def test_explainer_visual_template_example_and_page_example_pass(self):
        self.assertEqual(self.codes(EXAMPLE), [])
        ex = design()["view"]["explainer"]
        page = "\n".join(["## Explainer", "", ex["summary"], ""] + ["- " + x for x in ex["text"]] +
                         ["", "### Before", "", "```mermaid", ex["mermaid_before"].rstrip("\n"), "```", "", "### After", "", "```mermaid",
                          ex["mermaid_after"].rstrip("\n"), "```", "", ex["pr_legend_line"]])
        self.assertEqual(self.codes(page), [])
        self.assertEqual(ex["pr_legend_line"], prbody.LEGEND)

    def test_explainer_visual_draft_skeleton_follows_the_spec(self):
        sec = prbody.explainer_section(["dependency_change: human review needed: dependency change (lockfile uv.lock), never auto-merged."])
        before, after = sec.split("### After")
        self.assertTrue(sec.split("\n")[2].startswith("**What changes:**"))
        self.assertFalse([ln for ln in before.splitlines() if ln.strip().startswith("classDef")])
        for c in prbody.CLASSDEFS:
            self.assertIn("  " + c, after)
        self.assertTrue(sec.rstrip().endswith(prbody.LEGEND))
        for banned in ("fill:", "color:", "%%{init", "green", "amber", "red dashed"):
            self.assertNotIn(banned, sec)
        self.assertEqual(prbody.CLASSDEFS, ["classDef added stroke:#0969da,stroke-width:3px", "classDef changed stroke:#0969da,stroke-width:3px",
                                            "classDef removed stroke-width:2px,stroke-dasharray:6 4"])
        self.assertEqual(prbody.style_lines(self.body(sec)), [])     # agent markers left: no profile check yet

    def test_e2e_58_summarize_reasons_groups_more_than_three_and_leaves_few_unchanged(self):
        few = ["trust_boundary:auth:src/auth/a.py", "check_config:pyproject.toml#[tool.ruff]", "chain_approval"]
        self.assertEqual(prbody.summarize_reasons(few), "; ".join(few))
        self.assertEqual(prbody.summarize_reasons([]), "")
        many = [f"trust_boundary:ci_config:.github/workflows/w{i}.yml" for i in range(12)] + \
            [f"check_config:c{i}.toml" for i in range(5)] + ["dependency_change: dependency change (lockfile uv.lock)", "chain_approval"]
        line = prbody.summarize_reasons(many)
        self.assertEqual(line, "19 reasons: trust_boundary:ci_config (12: .github/workflows/w0.yml, .github/workflows/w1.yml, "
                               ".github/workflows/w2.yml and 9 more); check_config (5: c0.toml, c1.toml, c2.toml and 2 more); "
                               "dependency_change: dependency change (lockfile uv.lock); chain_approval")
        wide = [f"code{i}: d{i}" for i in range(10)]
        self.assertTrue(prbody.summarize_reasons(wide).endswith("; and 4 more groups (4 reasons)"))

    def test_explainer_visual_each_check_fires(self):
        e = EXAMPLE
        cases = {
            "no_summary_line": e.replace("**What changes:** adds", "Adds"),
            "before_has_markers": e.replace('match(["True or False"])\n  caller -->|login| cmp\n  cmp --> match\n```\n\n### After',
                                            'match(["True or False"]):::added\n  caller -->|login| cmp\n  cmp --> match\n```\n\n### After', 1),
            "palette_not_canonical": e.replace("  classDef removed", "  style cmp fill:#e3f4ea\n  classDef removed"),
            "legend_not_canonical": e.replace(prbody.LEGEND, "Legend: green = added · amber = changed · red dashed = removed"),
            "too_few_nodes": e.replace('  match(["True or False"])\n  caller -->|login| cmp\n  cmp --> match\n```\n\n### After',
                                       '  caller -->|login| cmp\n```\n\n### After', 1),
            "too_many_nodes": e.replace("  classDef added", "\n".join(f"  n{i}[\"Step {i}\"]:::added".replace("Step", "NEW<br/>Step")
                                                                      for i in range(5)) + "\n" +
                                        "\n".join(f"  accept ==> n{i}" for i in range(5)) + "\n  classDef added"),
            "too_many_arrows": e.replace("  classDef added", "\n".join("  caller -->|login| cmp" for _ in range(9)) + "\n  classDef added"),
            "label_has_path": e.replace('Compare passwords<br/>check_password()"]\n  match(["True or False"])\n  val',
                                        'Compare passwords<br/>auth/__init__.py:7"]\n  match(["True or False"])\n  val'),
            "label_too_long": e.replace("Check length is 8 or more", "Check that the new password is at least 8 characters"),
            "marker_without_tag": e.replace('reject(["NEW<br/>ValueError"])', 'reject(["ValueError"])'),
            "orphan_node": e.replace("  classDef added", '  lonely["NEW<br/>Log the attempt"]:::added\n  classDef added'),
            "no_entry": e.replace("  classDef added", '  other(["NEW<br/>Admin tool"]):::added\n  other ==> val\n  classDef added'),
            "before_ids_missing_in_after": e.replace("  cmp --> match\n```\n\n### After", '  hash["Hash the input<br/>hash()"]\n  cmp --> match\n'
                                                     '  cmp --> hash\n```\n\n### After', 1),
        }
        self.assertEqual(set(cases), set(STYLE_CODES))
        for code, text in cases.items():
            self.assertNotEqual(text, e, code)
            self.assertIn(code, self.codes(text), code)

    def test_explainer_visual_check_prints_one_line_per_problem_and_a_next(self):
        sb = Sandbox({"README.md": "# demo\n"})
        self.addCleanup(sb.close)
        bad = EXAMPLE.replace(prbody.LEGEND, "Legend: green = added").replace('reject(["NEW<br/>ValueError"])', 'reject(["ValueError"])')
        write(sb.repo, ".agents/state/x.md", bad)
        p = sb.run(["sh", sb.script("pr", "pr-body.sh"), "check", ".agents/state/x.md", "--explain-only"])
        self.assertEqual(p.returncode, 1, p.stdout)
        lines = p.stdout.strip().splitlines()
        self.assertTrue(lines[0].startswith("PROBLEM explainer_style:legend_not_canonical: the line right under the After diagram"), lines)
        self.assertTrue(lines[1].startswith("PROBLEM explainer_style:marker_without_tag: After node(s) reject"), lines)
        self.assertEqual(lines[-1], "AGENTIVE_PR=check_failed problems=2; NEXT=" + prbody.STYLE_NEXT)
        write(sb.repo, ".agents/state/x.md", EXAMPLE)
        p = sb.run(["sh", sb.script("pr", "pr-body.sh"), "check", ".agents/state/x.md", "--explain-only"])
        self.assertEqual(p.returncode, 0, p.stdout)

    def test_explainer_visual_ci_stays_presence_only(self):
        """RV8 (review/pr_body.explainer) checks presence only: a body that fails every profile check still passes CI."""
        from agentive.review import pr_body
        bad = self.body(EXAMPLE.replace("**What changes:** adds", "Adds").replace(prbody.LEGEND, "Legend: green = added")
                        .replace("  classDef added stroke:#0969da,stroke-width:3px", "  classDef added fill:#e3f4ea,stroke:#1d7a55"))
        self.assertTrue(prbody.explainer_style_problems(bad))
        present, probs = pr_body.explainer(bad)
        self.assertEqual(probs, [])
        self.assertTrue(all(present.values()))


class FlowFixture(unittest.TestCase):
    """A sandbox repo with a fake gh and the helpers Flow and AdoptDraft share. It holds no test: AdoptDraft used to inherit Flow
    itself and so ran every Flow test a second time with the same setUp."""

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

    def verify_json(self, head=None, dirty=False, tree=None):
        """The record `.agents/bin/verb verify` writes; content_tree defaults to HEAD^{tree} (VERIFYTREE)."""
        head = head or git(self.sb.repo, "rev-parse", "HEAD")
        doc = {"run_id": "r1", "ts": "2026-10-28T00:00:00Z", "total_failures": 0, "head_sha": head, "worktree_dirty": dirty,
               "verbs": {"lint": {"ok": True, "failures": 0, "duration_ms": 5, "cmd": "ruff check ."},
                         "test": {"ok": True, "failures": 0, "duration_ms": 9, "cmd": "pytest -q"}},
               "pending": ["typecheck"], "content_tree": tree or git(self.sb.repo, "rev-parse", "HEAD^{tree}")}
        if tree == "":
            doc.pop("content_tree")
        self.assertEqual(schema_errors("verify-result", doc), [])
        write(self.sb.repo, ".agents/state/verify.json", json.dumps(doc))

    def draft(self, *extra):
        p = self.sb.run(["sh", self.sb.script("pr", "pr-body.sh"), "draft", "--tool", "claude_code", "--model", "opus", *extra], env=self.env)
        return p.returncode, p.stdout


class Flow(FlowFixture):
    def test_local_estimate_uses_the_secpaths_floor_and_content_rules(self):
        """SECPATHS: the local tier estimate reads the same merged boundary as RV2: directory and token matches, case-sensitive,
        and a content rule from the block."""
        git(self.sb.repo, "switch", "-q", "-c", "feature/sec")
        self.commit({"secrets/prod.yaml": "a: 1\n", "src/Auth/x.py": "x = 1\n", "src/author.py": "y = 2\n"})
        self.verify_json()
        rc, out = self.draft()
        self.assertEqual(rc, 0, out)
        self.assertIn("trust_boundary:secrets:secrets/prod.yaml", out)
        self.assertNotIn("src/Auth/x.py", out)
        self.assertNotIn("src/author.py", out)
        self.assertIn("TIER=2", out)
        rules = prbody.rules_block(CODE_REVIEW.replace("    extend_defaults: true\n", "    extend_defaults: true\n    add:\n"
                                   "      - {path: \"**/*.py\", contains: jwt.decode, reason: auth}\n", 1))
        tb = rules["trust_boundary"]
        self.assertEqual([e["path"] for e in tb["content_rules"]], ["**/*.py"])
        self.assertIn({"token": "auth", "reason": "auth"}, tb["never_ai_approve"])

    def test_e2e_58_many_tier2_reasons_are_grouped_in_the_explainer_and_the_full_list_is_kept(self):
        """E2E-58: an install-sized PR (140 trust-boundary paths) puts one short grouped line in the Explainer bullet and in
        REASONS; every reason stays in REASONS_FILE."""
        git(self.sb.repo, "switch", "-q", "-c", "chore/install")
        files = {f"src/a{i}/secrets/k{i}.yaml": "a: 1\n" for i in range(70)}
        files.update({f"mod{i}/auth/x.py": "x = 1\n" for i in range(70)})
        self.commit(files)
        self.verify_json()
        rc, out = self.draft()
        self.assertEqual(rc, 0, out)
        reasons_line = [ln for ln in out.splitlines() if ln.startswith("REASONS ")][0]
        self.assertLess(len(reasons_line), 600, reasons_line)
        self.assertIn("trust_boundary:secrets (70: ", reasons_line)
        self.assertIn("trust_boundary:auth (70: ", reasons_line)
        self.assertIn("and 67 more)", reasons_line)
        self.assertIn("always_human: more than 100 changed files", reasons_line)
        self.assertIn(f"REASONS_FILE={prbody.REASONS_FILE} COUNT=141", out)
        full = read(self.sb.repo, *prbody.REASONS_FILE.split(os.sep)).splitlines()
        self.assertEqual(len(full), 141)         # 140 paths + always_human (more than 100 changed files)
        self.assertIn("trust_boundary:auth:mod69/auth/x.py", full)
        body = read(self.sb.repo, ".agents", "state", "pr-body.md")
        bullet = [ln for ln in body.splitlines() if ln.startswith("- Tier 2 (predicted):")][0]
        self.assertLess(len(bullet), 600, bullet)
        self.assertNotIn("mod69/auth/x.py", body)
        self.assertTrue(out.splitlines()[-1].startswith("PR_BODY="), out)

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
        self.assertEqual(heads, ["## Explainer", "## Verification", "## Risk", "## How to revert", "## agentive review record"])
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

    def test_e2e_11_pr_adding_the_review_workflow_says_it_gets_no_ai_review(self):
        """E2E-11 (WFCHANGE): the ## Risk section of a PR that adds or changes .github/workflows/agentive-review.yml says that the
        Claude Code action skips the AI review, a human reviews and merges, and the install PR is merged first; codex: not said."""
        git(self.sb.repo, "switch", "-q", "-c", "chore/agentive")
        self.commit({".github/workflows/agentive-review.yml": "name: agentive-review\n"})
        self.verify_json()
        rc, out = self.draft()
        self.assertEqual(rc, 0, out)
        body = read(self.sb.repo, ".agents", "state", "pr-body.md")
        risk = body.split("## Risk", 1)[1].split("## How to revert", 1)[0]
        self.assertIn(prbody.WORKFLOW_CHANGE_LINE, risk)
        for words in ("skips the CI AI review", "a human reviews and merges this PR", "Merge the install PR first"):
            self.assertIn(words, risk)
        write(self.sb.repo, ".agents/framework.yaml", "review:\n  pipeline:\n    ci_tool: codex\n")
        if git(self.sb.repo, "status", "--porcelain"):
            self.commit({}, "codex")
            self.verify_json()
        rc, out = self.draft()
        self.assertEqual(rc, 0, out)
        self.assertNotIn(prbody.WORKFLOW_CHANGE_LINE, read(self.sb.repo, ".agents", "state", "pr-body.md"))

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
        self.assertIn("TIER=uncertain EXPLAINER=optional", out)

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
               "reviewer": {"reviewer_id": "correctness-reviewer", "tool": "codex", "executed_by": "unknown", "attestation": "self_reported",
                            "context": "unverified", "session_id": "rev-1", "reviewed_ref": "abc123"}}
        rec["tree"] = rec["reviewer"]["reviewed_ref"] = git(self.sb.repo, "rev-parse", "HEAD^{tree}")      # E2E-44: bound to this tree
        self.assertEqual(schema_errors("precommit-review", rec), [])
        write(self.sb.repo, ".agents/state/precommit-review.json", json.dumps(rec))
        rc, out = self.draft()
        self.assertIn("U2=obtained", out)
        doc = block(read(self.sb.repo, ".agents", "state", "pr-body.md"))
        self.assertEqual(doc["agentive-review"]["reviews"][0]["source"], "precommit-review")
        rv = doc["agentive-review"]["reviews"][0]
        self.assertEqual(rv["session_id"], "rev-1")
        # INDEPCI: the PR-body record copies the honest local fields, never a hard-coded tool or context
        self.assertEqual((rv["tool"], rv["executed_by"], rv["attestation"], rv["context"]), ("codex", "unknown", "self_reported", "unverified"))
        self.assertEqual(rv["reviewed_ref"], rec["tree"])
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
        self.assertIn("\nintent skipped: user instruction\n", read(self.sb.repo, ".agents", "state", "pr-body.md"))

    def test_intent_skipped_parser_accepts_both_forms(self):
        # ENGLISHLAYER: the English line is current; the Korean line of agentive v0.1.0 still reads as intent-skipped
        def intent_skipped_in(body):
            return bool(prbody.INTENT_SKIPPED_RE.search(body or ""))
        old = "intent \uc0dd\ub7b5: \uc0ac\uc6a9\uc790 \uc9c0\uc2dc"
        self.assertTrue(intent_skipped_in("## Risk\n\nintent skipped: user instruction\n"))
        self.assertTrue(intent_skipped_in("## Risk\n\n" + old + "\n\n## How to revert\n"))
        self.assertTrue(intent_skipped_in("## Risk\nrisk text\n  intent skipped: user instruction  \n"))
        self.assertFalse(intent_skipped_in("## Risk\n\nthe intent was not skipped\n"))
        self.assertFalse(intent_skipped_in("a note: intent skipped: user instruction is the line /pr writes"))
        self.assertFalse(intent_skipped_in(""))
        self.assertFalse(intent_skipped_in(None))
        self.assertEqual(prbody.INTENT_SKIPPED_LINE, "intent skipped: user instruction")

    def test_stale_or_dirty_verify_is_not_reused(self):
        git(self.sb.repo, "switch", "-q", "-c", "fix/stale")
        self.commit({"src/app.py": "x = 4\n"})
        self.verify_json(tree=git(self.sb.repo, "rev-parse", "HEAD~1^{tree}"))     # verify saw other content
        rc, out = self.draft()
        self.assertEqual(rc, 1)
        self.assertIn("reason=verify_stale", out)
        self.assertIn("NEXT=run .agents/bin/verb verify --staged", out)
        self.verify_json(tree="")                                               # a record written before 2026-10-30
        self.assertIn("reason=verify_stale", self.draft()[1])
        self.verify_json()
        write(self.sb.repo, "src/app.py", "x = 5\n")
        self.assertIn("reason=verify_dirty", self.draft()[1])

    def test_e2e_08_commit_keeps_verify_reusable_for_pr(self):
        """VERIFYTREE (E2E-08, user 2026-10-30): verify on the dirty tree, stage, commit, /pr: the record is reused (no rerun),
        whatever head_sha and worktree_dirty it carries; an amend of the message keeps it, a rebase onto a moved base does not."""
        from agentive import hookio
        git(self.sb.repo, "switch", "-q", "-c", "feature/e2e08")
        write(self.sb.repo, "src/app.py", "x = 8\n")
        self.verify_json(head=git(self.sb.repo, "rev-parse", "HEAD"), dirty=True, tree=hookio.content_tree(self.sb.repo))
        git(self.sb.repo, "add", "-A")
        git(self.sb.repo, "commit", "-q", "-m", "change")
        rc, out = self.draft()
        self.assertEqual(rc, 0, out)
        self.assertNotIn("verify_stale", out)
        body = read(self.sb.repo, ".agents", "state", "pr-body.md")
        self.assertIn("Tree: " + git(self.sb.repo, "rev-parse", "HEAD^{tree}"), body)
        git(self.sb.repo, "commit", "-q", "--amend", "-m", "change, better message")    # same tree, new sha
        self.assertEqual(self.draft()[0], 0)
        git(self.sb.repo, "switch", "-q", "main")
        self.commit({"src/other.py": "y = 1\n"}, "main moves")
        git(self.sb.repo, "switch", "-q", "feature/e2e08")
        git(self.sb.repo, "rebase", "-q", "main")                                      # new base: a new tree
        rc, out = self.draft()
        self.assertEqual(rc, 1, out)
        self.assertIn("reason=verify_stale", out)

    def risk_of(self):
        body = read(self.sb.repo, ".agents", "state", "pr-body.md")
        return body, body.split("## Risk", 1)[1].split("## How to revert", 1)[0]

    def test_e2e_22_risk_starts_with_the_request_line(self):
        """E2E-22 (REQUESTLINE): ## Risk names where the change was asked for: a marker the agent fills, --request, the chain link."""
        git(self.sb.repo, "switch", "-q", "-c", "feat/table")
        self.commit({"src/table.py": "T = 1\n"})
        self.verify_json()
        rc, out = self.draft()
        self.assertEqual(rc, 0, out)
        body, risk = self.risk_of()
        self.assertEqual(risk.strip().splitlines()[0], prbody.REQUEST_MARKER)
        self.assertEqual(self.sb.run(["sh", self.sb.script("pr", "pr-body.sh"), "check", ".agents/state/pr-body.md"]).returncode, 1)
        rc, out = self.draft("--request", "user request: \"add a lookup  table\"")
        self.assertEqual(rc, 0, out)
        body, risk = self.risk_of()
        self.assertEqual(risk.strip().splitlines()[0], 'Request: user request: "add a lookup table"')
        self.assertNotIn("<!-- agent: Request:", body)
        self.assertEqual(prbody.request_line({"slug": "rate", "stage": "feature"}), "Request: docs/plans/rate/ (feature stage of the chain task)")
        self.assertEqual(prbody.request_line(None, hotfix_of=7), "Request: hotfix of PR #7")

    def test_e2e_23_draft_folds_the_review_record_and_passes_check(self):
        """E2E-23 (REVIEWRECORD): the block follows ## How to revert under its own heading, inside <details>; check passes."""
        git(self.sb.repo, "switch", "-q", "-c", "fix/rec")
        self.commit({"src/app.py": "x = 2\n"})
        self.verify_json()
        rc, out = self.draft("--request", "https://github.com/o/r/issues/1")
        self.assertEqual(rc, 0, out)
        body = read(self.sb.repo, ".agents", "state", "pr-body.md")
        tail = body.split("## How to revert", 1)[1]
        self.assertIn("\n## agentive review record\n\n<details><summary>" + prbody.REVIEW_RECORD_SUMMARY + "</summary>\n\n```yaml agentive-review\n", tail)
        self.assertTrue(body.rstrip().endswith("```\n\n</details>"))
        self.assertEqual(schema_errors("pr-body-agentive-review", block(body)), [])
        write(self.sb.repo, ".agents/state/pr-body.md", fill(body))
        p = self.sb.run(["sh", self.sb.script("pr", "pr-body.sh"), "check", ".agents/state/pr-body.md"])
        self.assertEqual(p.returncode, 0, p.stdout)

    def test_openmark_spec_pr_lists_the_open_questions_in_risk(self):
        """OPENMARK: the draft ## Risk of a spec PR lists every `- [OPEN]` bullet of the spec, so the question is read in the PR;
        answered questions are not listed, and a feature PR (or a spec without the marker) gets no such lines."""
        git(self.sb.repo, "switch", "-q", "main")
        self.commit({"docs/plans/openq/intent.md": "# Intent\n"}, "intent merged")
        git(self.sb.repo, "push", "-q", "origin", "main")
        git(self.sb.repo, "switch", "-q", "-c", "spec/openq")
        spec = ("Intent: docs/plans/openq/intent.md\n\n## Requirements\n\n- R1 keep the rows. Verification: test_rows\n\n"
                "## Open questions from the intent\n\n- Which index? -> answered: btree\n- [OPEN] May the migration drop the legacy users table?\n"
                "* [OPEN] Is the retention period 30 or 90 days?\n")
        self.commit({"docs/plans/openq/spec.md": spec})
        self.verify_json()
        rc, out = self.draft()
        self.assertEqual(rc, 0, out)
        self.assertIn("CHAIN=openq:spec", out)
        body, risk = self.risk_of()
        self.assertEqual(risk.strip().splitlines()[0], "Request: docs/plans/openq/ (spec stage of the chain task)")
        self.assertIn("Open questions in docs/plans/openq/spec.md ([OPEN], a human answers them before merging):\n"
                      "- [OPEN] May the migration drop the legacy users table?\n* [OPEN] Is the retention period 30 or 90 days?\n", risk)
        self.assertNotIn("Which index?", risk)
        git(self.sb.repo, "switch", "-q", "-c", "spec/clean", "main")
        self.commit({"docs/plans/openq/spec.md": spec.replace("- [OPEN]", "- answered:").replace("* [OPEN]", "* answered:")})
        self.verify_json()
        self.assertEqual(self.draft()[0], 0)
        self.assertNotIn("Open questions in docs/plans", self.risk_of()[1])

    PYPROJECT = '[project]\nname = "demo"\nversion = "0.1.0"\ndependencies = ["httpx>=0.27"]\n\n[tool.ruff]\nline-length = 100\n'

    def dep_base(self):
        git(self.sb.repo, "switch", "-q", "main")
        self.commit({"pyproject.toml": self.PYPROJECT, "uv.lock": "version = 1\n"}, "deps base")
        git(self.sb.repo, "push", "-q", "origin", "main")

    def test_e2e_29_dependency_and_lockfile_prs_are_predicted_tier2_with_an_explainer(self):
        """E2E-29 (LOCALTIER): the local estimate applies DEPHUMAN like CI: a new dependency (pyproject + uv.lock) and a
        lockfile-only change are TIER=2 EXPLAINER=required, the draft starts with ## Explainer, publish names dependency_change."""
        self.dep_base()
        git(self.sb.repo, "switch", "-q", "-c", "deps/humanize")
        self.commit({"pyproject.toml": self.PYPROJECT.replace('["httpx>=0.27"]', '["httpx>=0.27", "humanize>=4"]'),
                     "uv.lock": "version = 1\n# humanize 4.12\n"})
        self.verify_json()
        rc, out = self.draft()
        self.assertEqual(rc, 0, out)
        self.assertIn("TIER=2 EXPLAINER=required", out)
        self.assertIn("REASONS dependency_change: human review needed: dependency change (added pypi:humanize; lockfile uv.lock), "
                      "never auto-merged.", out)
        body = read(self.sb.repo, ".agents", "state", "pr-body.md")
        self.assertTrue(body.startswith("## Explainer\n"), body[:80])
        human, reasons = prbody.human_merge(prbody.Repo(self.sb.repo, fetch=False))       # what publish prints
        self.assertTrue(human)
        self.assertIn("why=dependency_change;", prbody.merge_lines(14, "https://x/14", True, reasons, "next")[0])
        git(self.sb.repo, "switch", "-q", "main")
        git(self.sb.repo, "switch", "-q", "-c", "deps/lock-only")
        self.commit({"uv.lock": "version = 1\n# pytest 9.1.1\n"})
        self.verify_json()
        rc, out = self.draft()
        self.assertIn("TIER=2 EXPLAINER=required", out)
        self.assertIn("dependency change (lockfile uv.lock)", out)

    def test_e2e_30_local_estimate_and_ci_classification_share_one_code_path(self):
        """E2E-30 (LOCALTIER): a [tool.ruff] edit is config and a new test file is test, as in CI (both tier 1, no Explainer);
        a source file the RV3 rules leave undecided stays uncertain (CI asks its LLM classifier). The draft's tier is the one
        review/pipeline.rules_estimate computes, the function the CI pipeline's boundary and classify steps are made of."""
        from agentive.review import pipeline
        self.dep_base()
        for branch, files, tier, reason in (
                ("chore/ruff", {"pyproject.toml": self.PYPROJECT.replace("line-length = 100", "line-length = 120")}, "1", "classes config"),
                ("test/table", {"tests/test_table.py": "def test_x():\n    assert 1\n"}, "1", "classes test"),
                ("fix/app2", {"src/app.py": "x = 7\n"}, "uncertain", prbody.UNCERTAIN_SOURCE.format(files="src/app.py"))):
            git(self.sb.repo, "switch", "-q", "main")
            git(self.sb.repo, "switch", "-q", "-c", branch)
            self.commit(files)
            self.verify_json()
            rc, out = self.draft()
            self.assertEqual(rc, 0, out)
            self.assertIn(f"TIER={tier} EXPLAINER={'required' if tier == '2' else 'optional'}", out)
            self.assertIn(f"REASONS {reason}", out)
            base = git(self.sb.repo, "merge-base", "origin/main", "HEAD")
            est = pipeline.rules_estimate(self.sb.repo, base, git(self.sb.repo, "rev-parse", "HEAD"),
                                          lambda rel: prbody.read(os.path.join(self.sb.repo, rel)) if rel == "code_review.md" else None,
                                          {"run": "interactive", "human_confirmation": "not_required", "confirm_scope_hits": []})
            self.assertEqual(str(est["tier"]), tier, branch)
        src = read(SRC, "dot-agents", "lib", "agentive", "review", "pipeline.py")
        run_body = src.split("def run(ctx):", 1)[1]
        self.assertIn("boundary(diff, rv1, rules, manifest", run_body)          # CI runs the same RV2 and RV3 steps
        self.assertIn("classify(diff, rv1, rules, manifest, rv2)", run_body)


    def test_e2e_30b_uncertain_source_file_says_why_and_what_ci_does(self):
        """E2E-30 rest (LOCALTIER, #4 of the E2E run): an ordinary source file stays uncertain because only the content can make
        it tier 2 (the RV3 LLM classifier calling it security, the RV4 raise-only check), which no deterministic rule can
        settle safely. The draft says so in plain words, names the file and what CI will do, and no longer requires the Explainer (E2E-65)."""
        self.dep_base()
        git(self.sb.repo, "switch", "-q", "-c", "feat/cart")
        self.commit({"src/shop/cart.py": "def total(items):\n    return sum(items)\n"})
        self.verify_json()
        rc, out = self.draft()
        self.assertEqual(rc, 0, out)
        self.assertIn("TIER=uncertain EXPLAINER=optional", out)
        reasons = [ln for ln in out.splitlines() if ln.startswith("REASONS ")][0]
        for part in ("class undecided by rules: src/shop/cart.py.", "CI asks its LLM classifier", "tier 1 unless it calls the change a "
                     "security change or the raise-only check asks for a human", "then tier 2 and the Explainer is required",
                     "The Explainer is optional now; if CI decides tier 2, the gate blocks with explainer_missing and /pr --explain adds it"):
            self.assertIn(part, reasons)
        self.assertNotIn("LLM layer", reasons)
        self.assertFalse(read(self.sb.repo, ".agents", "state", "pr-body.md").startswith("## Explainer\n"))   # E2E-65: optional when uncertain
        # what the rules can settle is still settled: a source file under a security path is tier 2 by the path rule, no LLM needed
        git(self.sb.repo, "switch", "-q", "main")
        git(self.sb.repo, "switch", "-q", "-c", "feat/auth")
        self.commit({"src/shop/auth/login.py": "def login():\n    return 1\n"})
        self.verify_json()
        rc, out = self.draft()
        self.assertIn("TIER=2 EXPLAINER=required", out)
        self.assertNotIn("CI asks its LLM classifier", out)
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


class PublishFixture(unittest.TestCase):
    """The sandbox repo and the helpers of the publish tests (no tests of its own)."""

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
        self.refresh = "## Verification\n\nHEAD: abc\n- lint: ok\n"       # the shape of a MODE=refresh draft (E2E-50: a full body no longer refreshes)

    def tearDown(self):
        self.sb.close()

    def publish(self, env, *extra):
        return self.sb.run(["sh", self.sb.script("pr", "pr-body.sh"), "publish", ".agents/state/body.md", *extra], env=env)

    def open_pr_routes(self, number, body):
        return self.sb.fake_gh([(r"pulls\?head=", {"stdout": [{"number": number}]}), (r"-X PATCH", {"stdout": {"number": number}}),
                                (rf"pulls/{number}$", {"stdout": {"number": number, "html_url": f"https://x/{number}", "body": body}})])


class Publish(PublishFixture):
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
        write(self.sb.repo, ".agents/state/body.md", self.refresh)
        p = self.publish(env)
        self.assertEqual(p.returncode, 0, p.stdout)
        self.assertIn("next: /pr-feedback 7", p.stdout)
        new = [c for c in self.sb.gh_calls() if "PATCH" in c["args"]][0]["body"]
        self.assertIn("HEAD: abc", new)
        self.assertNotIn("\nold\n", new)
        for kept in ("## Explainer", "kept risk", "pipeline: kept", "### Before"):
            self.assertIn(kept, new)

    def human_merge_branch(self):
        git(self.sb.repo, "switch", "-q", "-c", "feature/auth")
        write(self.sb.repo, "src/auth/login.py", "LIMIT = 5\n")
        git(self.sb.repo, "add", "-A")
        git(self.sb.repo, "commit", "-q", "-m", "auth")
        git(self.sb.repo, "push", "-q", "-u", "origin", "feature/auth")
        return self.sb.fake_gh([(r"pulls\?head=", {"stdout": []}),
                                (r"-X POST .*pulls", {"stdout": {"number": 21, "html_url": "https://x/21"}})])

    def test_e2e_14_tier2_publish_says_a_human_merges(self):
        """E2E-14 (HUMANMERGE): a tier 2 PR (here a trust-boundary path) ends with MERGE=human and the URL; with a human present the
        last line stays the /pr-feedback line; a tier 0 PR gets no MERGE line."""
        p = self.publish(self.human_merge_branch(), "--title", "feat: limit")
        self.assertEqual(p.returncode, 0, p.stdout)
        lines = p.stdout.strip().splitlines()
        self.assertEqual(lines[0], "PR=21 URL=https://x/21 CREATED")
        self.assertTrue(lines[1].startswith("MERGE=human pr=21 url=https://x/21 why=trust_boundary; NEXT=tell the human to review and merge https://x/21"), lines)
        self.assertIn("agents never merge it", lines[1])
        self.assertTrue(lines[-1].startswith("next: /pr-feedback 21 after CI finishes"), lines)
        self.assertNotIn("waiting=human_merge", p.stdout)

    def test_e2e_14_headless_publish_reports_the_url_and_ends(self):
        """E2E-14: with no human present (AGENTIVE_HEADLESS=1: headless runs (`claude -p`), marked Routines runs) the last line is
        AGENTIVE_PR=done ... waiting=human_merge with the URL, so the run ends instead of looking for a way to merge."""
        env = dict(self.human_merge_branch(), AGENTIVE_HEADLESS="1")
        p = self.publish(env, "--title", "feat: limit")
        self.assertEqual(p.returncode, 0, p.stdout)
        last = p.stdout.strip().splitlines()[-1]
        self.assertEqual(last, "AGENTIVE_PR=done pr=21 waiting=human_merge url=https://x/21; NEXT=report the PR URL and end the run "
                               "(a human reviews and merges it; nothing in this run waits for the merge)")
        self.assertIn("MERGE=human pr=21", p.stdout)
        self.assertNotIn("next: /pr-feedback", p.stdout)

    def test_e2e_14_tier0_publish_has_no_merge_line(self):
        env = self.sb.fake_gh([(r"pulls\?head=", {"stdout": []}),
                               (r"-X POST .*pulls", {"stdout": {"number": 12, "html_url": "https://x/12"}})])
        p = self.publish(dict(env, AGENTIVE_HEADLESS="1"), "--title", "docs: readme")
        self.assertEqual(p.returncode, 0, p.stdout)
        self.assertNotIn("MERGE=human", p.stdout)
        self.assertEqual(p.stdout.strip().splitlines()[-1], "next: /pr-feedback 12 after CI finishes")

    def test_e2e_14_refresh_of_a_tier2_pr_keeps_saying_a_human_merges(self):
        env = self.human_merge_branch()
        env = self.sb.fake_gh([(r"pulls\?head=", {"stdout": [{"number": 21}]}), (r"-X PATCH", {"stdout": {"number": 21}}),
                               (r"pulls/21$", {"stdout": {"number": 21, "html_url": "https://x/21", "body": "## Verification\n\nold\n"}})])
        write(self.sb.repo, ".agents/state/body.md", self.refresh)
        p = self.publish(env)
        self.assertEqual(p.returncode, 0, p.stdout)
        self.assertIn("PR=21 UPDATED=Verification", p.stdout)
        self.assertIn("MERGE=human pr=21 url=https://x/21", p.stdout)
        self.assertTrue(p.stdout.strip().splitlines()[-1].startswith("next: /pr-feedback 21 (fixes only"), p.stdout)

    def test_e2e_50_full_draft_with_an_open_pr_that_holds_a_pr_body_stops_and_writes_nothing(self):
        """E2E-50: the draft was made in MODE=new (PR=none), then an open PR that already holds a /pr body appeared: publish replaced
        only ## Verification, so the drafted Explainer, Risk and review record were lost and the run ended as a success. Now it stops
        with a NEXT that names draft and --replace-body, and no PATCH is sent. (A person's body without /pr sections is adopted,
        PRADOPT: the tests below.)"""
        env = self.open_pr_routes(1, "## Verification\n\nold\n\n## Risk\n\nkept\n")
        p = self.publish(env, "--title", "t")
        self.assertEqual(p.returncode, 1, p.stdout)
        last = p.stdout.strip().splitlines()[-1]
        self.assertTrue(last.startswith("AGENTIVE_PR=stop reason=pr_opened_since_draft pr=1; NEXT="), last)
        self.assertIn("--replace-body", last)
        self.assertNotIn("UPDATED", p.stdout)
        self.assertEqual([c for c in self.sb.gh_calls() if "PATCH" in c["args"]], [])

    def test_e2e_50_replace_body_writes_the_whole_draft_and_keeps_the_old_body(self):
        old = "feat: install\n\nthe commit message"
        p = self.publish(self.open_pr_routes(1, old), "--replace-body")
        self.assertEqual(p.returncode, 0, p.stdout)
        self.assertIn("PR=1 UPDATED=body saved=.agents/state/pr-body-before-1.md", p.stdout)
        self.assertEqual([c for c in self.sb.gh_calls() if "PATCH" in c["args"]][0]["body"], self.full)
        self.assertEqual(read(self.sb.repo, ".agents", "state", "pr-body-before-1.md"), old)
        self.assertIn("next: /pr-feedback 1", p.stdout)

    def test_e2e_50_refresh_draft_still_replaces_only_verification(self):
        write(self.sb.repo, ".agents/state/body.md", self.refresh)
        p = self.publish(self.open_pr_routes(2, "## Verification\n\nold\n\n## Risk\n\nkept\n"))
        self.assertEqual(p.returncode, 0, p.stdout)
        self.assertIn("PR=2 UPDATED=Verification", p.stdout)
        new = [c for c in self.sb.gh_calls() if "PATCH" in c["args"]][0]["body"]
        self.assertIn("kept", new)

    def test_branch_must_be_pushed(self):
        git(self.sb.repo, "switch", "-q", "-c", "docs/unpushed")
        env = self.sb.fake_gh([])
        p = self.publish(env, "--title", "t")
        self.assertEqual(p.returncode, 1)
        self.assertIn("NEXT=git push -u origin docs/unpushed", p.stdout)


class Adopt(PublishFixture):
    """PRADOPT (user 2026-11-01): /pr on an open PR whose body has none of the /pr sections (opened on the GitHub web UI with the
    commit message as body) writes the whole /pr body and keeps the old text, quoted, under ## Original description. These tests
    fail on 5bd987a: publish stopped (full draft) or inserted ## Verification into the person's text (refresh draft)."""

    HUMAN = "feat: install\n\nThe commit message.\n\n## Notes\n\n- keep this\n"

    def patched(self):
        return [c for c in self.sb.gh_calls() if "PATCH" in c["args"]]

    def adopt(self, human, *extra):
        p = self.publish(self.open_pr_routes(1, human), *extra)
        return p, (self.patched()[0]["body"] if self.patched() else None)

    def test_body_without_pr_sections_gets_the_full_body_and_the_quoted_original(self):
        p, new = self.adopt(self.HUMAN)
        self.assertEqual(p.returncode, 0, p.stdout)
        self.assertIn("PR=1 UPDATED=body saved=.agents/state/pr-body-before-1.md original=kept", p.stdout)
        self.assertTrue(new.startswith(self.full.rstrip("\n")), new)
        tail = new[len(self.full.rstrip("\n")):]
        self.assertEqual(tail, "\n\n## Original description\n\n> feat: install\n>\n> The commit message.\n>\n> ## Notes\n>\n> - keep this\n")
        self.assertEqual(read(self.sb.repo, ".agents", "state", "pr-body-before-1.md"), self.HUMAN)
        self.assertEqual(prbody.check_body(new, "optional"), [])      # the person's ## Notes is quoted: no extra section, no marker, one block

    def test_a_person_heading_is_quoted_not_parsed_and_tier2_keeps_the_explainer_first(self):
        explainer = ("## Explainer\n\n**What changes:** x\n\n### Before\n\n```mermaid\nflowchart TD\n  a --> b\n```\n\n"
                     "### After\n\n```mermaid\nflowchart TD\n  a --> b\n```\n\n")
        write(self.sb.repo, ".agents/state/body.md", explainer + self.full)
        p, new = self.adopt("## Explainer-ish title\n\ntext\n\n```\n## Risk\n```\n")
        self.assertEqual(p.returncode, 0, p.stdout)
        heads = [ln for ln, inside in prbody.fences(new.splitlines()) if not inside and ln.startswith("## ")]
        self.assertEqual(heads, ["## Explainer", "## Verification", "## Risk", "## How to revert", "## Original description"])
        self.assertEqual(prbody.explainer_problems(new), [])
        self.assertEqual(prbody.block_problems(new), [])               # the person's fenced ## Risk is quoted too: still one agentive-review block
        self.assertIn("> ## Explainer-ish title", new)

    def test_crlf_body_is_quoted_line_by_line(self):
        p, new = self.adopt("title\r\n\r\nline two\r\n")
        self.assertEqual(p.returncode, 0, p.stdout)
        self.assertNotIn("\r", new)
        self.assertTrue(new.endswith("## Original description\n\n> title\n>\n> line two\n"), new)

    def test_second_refresh_does_not_nest_or_duplicate_the_original_description(self):
        p, first = self.adopt(self.HUMAN)
        self.assertEqual(p.returncode, 0, p.stdout)
        # the next run: the PR now holds a /pr body, so draft is a refresh and publish splices ## Verification only
        write(self.sb.repo, ".agents/state/body.md", self.refresh)
        self.sb.fake_gh([(r"pulls\?head=", {"stdout": [{"number": 1}]}), (r"-X PATCH", {"stdout": {"number": 1}}),
                         (r"pulls/1$", {"stdout": {"number": 1, "html_url": "https://x/1", "body": first}})])
        env = self.open_pr_routes(1, first)
        p2 = self.publish(env)
        self.assertEqual(p2.returncode, 0, p2.stdout)
        second = self.patched()[-1]["body"]
        self.assertEqual(second.count("## Original description"), 1)
        self.assertIn("> ## Notes", second)
        self.assertNotIn("> >", second)
        # a full draft published again over the adopted body stops (nothing is nested)
        write(self.sb.repo, ".agents/state/body.md", self.full)
        before = len(self.patched())
        p3 = self.publish(env, "--title", "t")
        self.assertEqual(p3.returncode, 1, p3.stdout)
        self.assertIn("reason=pr_opened_since_draft", p3.stdout)
        self.assertEqual(len(self.patched()), before)
        # and a body that is nothing but the quoted section (the sections were deleted by hand) is not quoted twice
        write(self.sb.repo, ".agents/state/body.md", self.full)
        only = first[first.index("## Original description"):]
        p4, again = self.adopt(only)
        self.assertEqual(p4.returncode, 0, p4.stdout)
        self.assertEqual(again.count("## Original description"), 1)
        self.assertNotIn("> >", again)

    def test_replace_body_keeps_meaning_no_original_description_section(self):
        p, new = self.adopt(self.HUMAN, "--replace-body")
        self.assertEqual(p.returncode, 0, p.stdout)
        self.assertEqual(new, self.full)
        self.assertNotIn("original=kept", p.stdout)
        self.assertEqual(read(self.sb.repo, ".agents", "state", "pr-body-before-1.md"), self.HUMAN)

    def test_empty_body_adds_no_original_description_section(self):
        p, new = self.adopt("")
        self.assertEqual(p.returncode, 0, p.stdout)
        self.assertEqual(new, self.full)

    def test_refresh_draft_on_a_person_body_stops_with_a_draft_next(self):
        write(self.sb.repo, ".agents/state/body.md", self.refresh)
        p, new = self.adopt(self.HUMAN)
        self.assertEqual(p.returncode, 1, p.stdout)
        self.assertTrue(p.stdout.strip().splitlines()[-1].startswith("AGENTIVE_PR=stop reason=pr_body_not_agentive pr=1; NEXT="), p.stdout)
        self.assertIsNone(new)

    def test_body_edited_between_draft_and_publish_stops_and_writes_nothing(self):
        """The draft records the fingerprint of the body it saw (.agents/state/pr-body-seen-<n>.sha256); publish compares it with the
        body it reads right before the PATCH. GitHub's REST PATCH has no conditional update [미검증], so the window between that read
        and the PATCH (milliseconds) stays open."""
        write(self.sb.repo, ".agents/state/pr-body-seen-1.sha256", prbody.body_fingerprint(self.HUMAN) + "\n")
        p, new = self.adopt(self.HUMAN + "\nA person added a line.\n")
        self.assertEqual(p.returncode, 1, p.stdout)
        last = p.stdout.strip().splitlines()[-1]
        self.assertTrue(last.startswith("AGENTIVE_PR=stop reason=pr_body_changed_since_draft pr=1; NEXT="), last)
        self.assertIn("pr-body.sh draft", last)
        self.assertIsNone(new)
        self.assertFalse(os.path.exists(os.path.join(self.sb.repo, ".agents", "state", "pr-body-before-1.md")))
        # unchanged since the draft (CRLF alone is no change): published, fingerprint removed
        p2, new2 = self.adopt(self.HUMAN.replace("\n", "\r\n"))
        self.assertEqual(p2.returncode, 0, p2.stdout)
        self.assertIn("> - keep this", new2)
        self.assertFalse(os.path.exists(os.path.join(self.sb.repo, ".agents", "state", "pr-body-seen-1.sha256")))

    def test_pr_body_detection(self):
        for body in ("", "plain text", "## Notes\n\ntext", "```\n## Verification\n```\n", "> ## Risk\n"):
            self.assertFalse(prbody.is_pr_body(body), body)
        for body in ("## Verification\n", "x\r\n## Risk\r\n", "## agentive review record\n",
                     "```yaml agentive-review\nagentive-review: {}\n```\n"):
            self.assertTrue(prbody.is_pr_body(body), body)


class AdoptDraft(FlowFixture):
    """PRADOPT, draft side: an open PR whose body has none of the /pr sections gets the whole body (MODE=adopt)."""

    def open_pr(self, body):
        git(self.sb.repo, "switch", "-q", "-c", "feature/auth")
        self.commit({"src/auth/limit.py": "LIMIT = 5\n"})
        self.verify_json()
        self.env = self.sb.fake_gh([(r"pulls\?head=", {"stdout": [{"number": 9}]}),
                                    (r"pulls/9$", {"stdout": {"number": 9, "body": body}})])

    def test_person_body_makes_a_full_tier2_draft_and_records_the_fingerprint(self):
        self.open_pr("feat: auth\n\nthe commit message")
        rc, out = self.draft()
        self.assertEqual(rc, 0, out)
        self.assertIn("MODE=adopt PR=9 TIER=2 EXPLAINER=required", out)
        self.assertIn("NOTE adopt pr=9", out)
        self.assertEqual(len([ln for ln in out.splitlines() if "NEXT=" in ln]), 1)
        body = read(self.sb.repo, ".agents", "state", "pr-body.md")
        heads = [ln for ln in body.splitlines() if ln.startswith("## ")]
        self.assertEqual(heads, ["## Explainer", "## Verification", "## Risk", "## How to revert", "## agentive review record"])
        self.assertEqual(read(self.sb.repo, ".agents", "state", "pr-body-seen-9.sha256").strip(), prbody.body_fingerprint("feat: auth\n\nthe commit message"))

    def test_pr_body_made_by_pr_stays_a_refresh_and_clears_a_stale_fingerprint(self):
        self.open_pr("## Verification\n\nold\n")
        write(self.sb.repo, ".agents/state/pr-body-seen-9.sha256", "stale\n")
        rc, out = self.draft()
        self.assertIn("MODE=refresh PR=9", out)
        self.assertFalse(os.path.exists(os.path.join(self.sb.repo, ".agents", "state", "pr-body-seen-9.sha256")))

    def test_e2e_59_draft_without_a_tool_flag_detects_the_tool(self):
        """E2E-59: the pr skill passes no --tool; the implementer record takes AGENTIVE_TOOL, then CLAUDECODE, else other."""
        self.open_pr("feat: auth")
        self.verify_json()
        cmd = ["sh", self.sb.script("pr", "pr-body.sh"), "draft", "--model", "opus"]
        for env_add, want in (({"AGENTIVE_TOOL": "codex"}, "codex"), ({"AGENTIVE_TOOL": "claude", "CLAUDECODE": ""}, "claude_code"),
                              ({"AGENTIVE_TOOL": "", "CLAUDECODE": "1"}, "claude_code"), ({"AGENTIVE_TOOL": "", "CLAUDECODE": ""}, "other")):
            env = dict(self.env, **env_add)
            self.assertEqual(self.sb.run(cmd, env=env).returncode, 0, env_add)
            imp = block(read(self.sb.repo, ".agents", "state", "pr-body.md"))["agentive-review"]["implementer"]
            self.assertEqual(imp["tool"], want, env_add)

    def test_review_record_names_the_pr_session_not_the_authors_of_the_commits(self):
        """Human commits mixed into the branch: the agentive-review implementer is the tool, model and session that ran /pr
        (--tool, --model, --session-id); no commit author is read or claimed. started_at is the first commit ahead of the base, whoever
        made it. Nothing reads implementer to attribute commits (independence is decided by the CI job's own review), so no change."""
        self.open_pr("feat: auth")
        git(self.sb.repo, "-c", "user.name=Human Dev", "-c", "user.email=human@example.com", "commit", "-q", "--allow-empty", "-m", "by a person")
        self.verify_json()
        rc, out = self.draft("--session-id", "sess-agent")
        self.assertEqual(rc, 0, out)
        imp = block(read(self.sb.repo, ".agents", "state", "pr-body.md"))["agentive-review"]["implementer"]
        self.assertEqual((imp["tool"], imp["model"], imp["session_id"]), ("claude_code", "opus", "sess-agent"))
        self.assertNotIn("human@example.com", read(self.sb.repo, ".agents", "state", "pr-body.md"))


class E2E06BaseRef(unittest.TestCase):
    """BASEREF (E2E-06, user 2026-10-30 '이렇게 해줘 (gh api 빼고 origin/HEAD 확인 -> ls-remote 두 단계)'): /pr draft fetches
    origin/<default> with an explicit refspec; a missing base is a stop with a NEXT, never 'no diff'."""

    def make(self, cls=Sandbox, files=None):
        self.sb = cls(files or {"README.md": "# demo\n", "code_review.md": CODE_REVIEW, "src/app.py": "x = 1\n"})
        self.addCleanup(self.sb.close)
        self.env = self.sb.fake_gh([])
        return self.sb

    def commit(self, files, msg="change"):
        for rel, text in files.items():
            write(self.sb.repo, rel, text)
        git(self.sb.repo, "add", "-A")
        git(self.sb.repo, "commit", "-q", "-m", msg)

    def verify(self):
        Flow.verify_json(self)

    def draft(self):
        p = self.sb.run(["sh", self.sb.script("pr", "pr-body.sh"), "draft", "--tool", "claude_code", "--model", "opus"], env=self.env)
        return p.returncode, p.stdout

    def ref(self, name):
        return self.sb.run(["git", "rev-parse", "--verify", "--quiet", name]).returncode == 0

    def test_e2e_06_depth_clone_of_an_empty_origin_gets_its_base_by_fetch(self):
        self.make(DepthSandbox)
        self.assertEqual(git(self.sb.repo, "config", "--get-all", "remote.origin.fetch") if self.sb.run(
            ["git", "config", "--get-all", "remote.origin.fetch"]).returncode == 0 else "", "")
        self.assertFalse(self.ref("refs/remotes/origin/main"))           # the E2E-06 state
        git(self.sb.repo, "switch", "-q", "-c", "fix/e2e06")
        self.commit({"src/app.py": "x = 2\n"})
        self.verify()
        rc, out = self.draft()
        self.assertEqual(rc, 0, out)
        self.assertNotIn("no diff", out)
        self.assertNotIn("empty diff", out)
        self.assertIn("TIER=", out)
        self.assertTrue(self.ref("refs/remotes/origin/main"))
        self.assertEqual(git(self.sb.repo, "symbolic-ref", "refs/remotes/origin/HEAD"), "refs/remotes/origin/main")   # set-head after the fetch
        self.assertEqual(git(self.sb.repo, "config", "--get-all", "remote.origin.fetch") if self.sb.run(
            ["git", "config", "--get-all", "remote.origin.fetch"]).returncode == 0 else "", "")           # config untouched

    def test_e2e_06_no_fetch_and_no_local_ref_is_a_stop_with_the_refspec_next(self):
        from agentive import gitgh
        self.make(DepthSandbox)
        git(self.sb.repo, "switch", "-q", "-c", "fix/e2e06")
        self.commit({"src/app.py": "x = 2\n"})
        orig = gitgh._fetch
        gitgh._fetch = lambda root, name: (False, "fatal: unable to access origin")   # the name is known (ls-remote), the fetch fails
        try:
            b = gitgh.base_ref(self.sb.repo)
        finally:
            gitgh._fetch = orig
        self.assertEqual(b["stop"], "AGENTIVE_PR=stop reason=base_ref_missing ref=refs/remotes/origin/main fetch=failed refspec=none; "
                                    "NEXT=git fetch origin +refs/heads/main:refs/remotes/origin/main, then rerun sh .agents/skills/pr/scripts/pr-body.sh draft")
        self.assertTrue(any(n.startswith("NOTE remote.origin.fetch is empty") and "git remote set-branches origin '*'" in n for n in b["notes"]), b)
        sh(["git", "fetch", "origin", "+refs/heads/main:refs/remotes/origin/main"], self.sb.repo)          # the NEXT works
        self.assertTrue(self.ref("refs/remotes/origin/main"))
        git(self.sb.repo, "remote", "set-url", "origin", self.sb.tmp + "/missing.git")
        git(self.sb.repo, "update-ref", "-d", "refs/remotes/origin/main")
        rc, out = self.draft()                                           # offline: not even the name is known
        self.assertEqual(rc, 1, out)
        self.assertIn("reason=no_default_branch", out.strip().splitlines()[-1])
        self.assertNotIn("TIER=", out)

    def test_e2e_06_failed_fetch_with_a_local_ref_notes_and_continues(self):
        self.make()
        git(self.sb.repo, "switch", "-q", "-c", "fix/offline")
        self.commit({"src/app.py": "x = 3\n"})
        self.verify()
        git(self.sb.repo, "remote", "set-url", "origin", self.sb.tmp + "/missing.git")
        rc, out = self.draft()
        self.assertEqual(rc, 0, out)
        self.assertIn("NOTE git fetch of main failed; using the local origin/main (may be stale)", out)

    def rename_origin_default(self):
        git(self.sb.origin, "branch", "-m", "main", "trunk")
        git(self.sb.origin, "symbolic-ref", "HEAD", "refs/heads/trunk")

    def test_e2e_06_dangling_origin_head_falls_back_to_the_remote_head(self):
        self.make()
        git(self.sb.repo, "switch", "-q", "-c", "fix/rename")
        self.commit({"src/app.py": "x = 4\n"})
        self.verify()
        self.rename_origin_default()
        git(self.sb.repo, "fetch", "-q", "--prune", "origin")             # origin/main is gone, origin/HEAD dangles
        self.assertFalse(self.ref("refs/remotes/origin/HEAD"))
        rc, out = self.draft()
        self.assertEqual(rc, 0, out)
        self.assertEqual(git(self.sb.repo, "symbolic-ref", "refs/remotes/origin/HEAD"), "refs/remotes/origin/trunk")

    def test_e2e_06_stale_origin_head_with_its_ref_still_local_is_detected_again(self):
        self.make()
        git(self.sb.repo, "switch", "-q", "-c", "fix/rename2")
        self.commit({"src/app.py": "x = 5\n"})
        self.verify()
        self.rename_origin_default()                                      # no prune: origin/main and origin/HEAD stay valid
        rc, out = self.draft()
        self.assertEqual(rc, 0, out)
        self.assertIn("NOTE origin/HEAD=main remote HEAD=trunk; using trunk", out)
        self.assertTrue(self.ref("refs/remotes/origin/trunk"))

    def test_e2e_06_shallow_history_without_a_merge_base(self):
        self.make()
        seed = os.path.join(self.sb.tmp, "seed")
        sh(["git", "clone", "-q", self.sb.origin, seed], self.sb.tmp)
        shallow = os.path.join(self.sb.tmp, "shallow")
        sh(["git", "clone", "-q", "--depth", "1", "file://" + self.sb.origin, shallow], self.sb.tmp)
        self.sb.repo = shallow
        self.sb.install()
        git(shallow, "switch", "-q", "-c", "fix/shallow")
        self.commit({"src/app.py": "x = 6\n"})
        for i in (1, 2):
            write(seed, f"m{i}.txt", "m\n")
            git(seed, "add", "-A")
            git(seed, "commit", "-q", "-m", f"main {i}")
        git(seed, "push", "-q", "origin", "main")
        git(shallow, "fetch", "-q", "--depth=1", "origin", "+refs/heads/main:refs/remotes/origin/main")
        git(shallow, "remote", "set-url", "origin", self.sb.tmp + "/missing.git")     # offline from here on
        rc, out = self.draft()
        self.assertEqual(rc, 1, out)
        self.assertTrue(out.strip().splitlines()[-1].startswith(
            "AGENTIVE_PR=stop reason=no_merge_base base=origin/main shallow=true; NEXT=git fetch --unshallow origin"), out)

    def test_e2e_06_no_commits_ahead_and_empty_diff_are_told_apart(self):
        self.make(files={"README.md": "# demo\n", "src/app.py": "x = 1\n"})     # the base has no code_review.md
        git(self.sb.repo, "switch", "-q", "-c", "fix/nothing")
        rc, out = self.draft()
        self.assertEqual(rc, 1, out)
        self.assertEqual(out.strip().splitlines()[-1], "AGENTIVE_PR=stop reason=no_commits_ahead base=origin/main; NEXT=commit the change "
                         "on this branch (or switch to the branch that holds it), then rerun sh .agents/skills/pr/scripts/pr-body.sh draft")
        self.assertNotIn("TIER=uncertain", out)
        self.commit({"src/app.py": "x = 9\n", "code_review.md": CODE_REVIEW}, "change")
        self.commit({"src/app.py": "x = 1\n"}, "undo")
        git(self.sb.repo, "rm", "-q", "code_review.md")
        git(self.sb.repo, "commit", "-q", "-m", "undo 2")
        self.verify()
        rc, out = self.draft()
        self.assertEqual(rc, 0, out)
        self.assertIn("TIER=uncertain", out)
        self.assertIn("empty diff: the 3 commit(s) ahead of origin/main change nothing in total", out)

    def test_e2e_06_rules_from_the_work_tree_are_named(self):
        self.make(files={"README.md": "# demo\n", "src/app.py": "x = 1\n"})
        git(self.sb.repo, "switch", "-q", "-c", "chore/install")
        self.commit({"code_review.md": CODE_REVIEW, "src/app.py": "x = 2\n"})
        self.verify()
        rc, out = self.draft()
        self.assertEqual(rc, 0, out)
        self.assertIn("NOTE rules from the work tree: origin/main has no code_review.md rules block (local estimate only; CI reads the base commit)", out)

    def test_e2e_06_default_branch_detection_order(self):
        from agentive import gitgh
        self.make()
        self.assertEqual(gitgh.default_branch(self.sb.repo), "main")
        self.assertEqual(gitgh._net_env()["GIT_TERMINAL_PROMPT"], "0")
        git(self.sb.repo, "remote", "set-head", "origin", "-d")
        self.assertEqual(gitgh.default_branch(self.sb.repo), "main")      # ls-remote --symref
        git(self.sb.repo, "remote", "set-url", "origin", self.sb.tmp + "/missing.git")
        self.assertIsNone(gitgh.default_branch(self.sb.repo))
        rc, out = self.draft()
        self.assertEqual((rc, out.strip().splitlines()[-1]), (1, "AGENTIVE_PR=stop reason=no_default_branch; NEXT=check that origin is reachable "
                                                                  "(git ls-remote --symref origin HEAD), then rerun sh .agents/skills/pr/scripts/pr-body.sh draft"))


class Splice(unittest.TestCase):
    def test_insert_explainer_first(self):
        out = prbody.splice("## Verification\n\nv\n", "## Explainer", "## Explainer\n\nnew")
        self.assertTrue(out.startswith("## Explainer\n\nnew\n\n## Verification"))


if __name__ == "__main__":
    unittest.main()
