# agentive-unit: selftest.component
# design: decisions.accepted[SECPATHS], spec.init_apply.security_evidence, spec.init_apply.update.custom_overrides, docs/impl/B.md §1
"""SECPATHS L1 and L3 in init and update: the CODEOWNERS snapshot (source codeowners:<line>), what update proposes when CODEOWNERS
changes, a project override (add / remove) that survives update, stack presets (Next.js version switch, Spring, Django) and the
security tools found in the repo. Runs the real engine from the generated Claude Code payload."""
import json
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from test_engine import Base, PAYLOAD  # noqa: E402

sys.path.insert(0, os.path.join(PAYLOAD, "engine"))
import secscan  # noqa: E402

from agentive import ayaml  # noqa: E402
from agentive.review import config as cfg, secpaths, util  # noqa: E402

CODEOWNERS = "# owners\n*.js @org/frontend\n/infra/ @org/security-team @bob\ndocs/ @org/appsec\n/payments/* @alice @org/sre\napps/ @octo\n!/x @org/security\n"


class SecScanBase(Base):
    def init(self, *extra):
        self.git("init", "-q")
        return self.engine("init", "--no-managed-review-copy", *extra)

    def block(self):
        text = self.read("code_review.md")
        return ayaml.loads(util.fenced_blocks(text, "yaml agentive-review-rules")[0])["agentive-review-rules"]

    def sourced(self):
        tb = self.block()["trust_boundary"]
        return [(e.get("glob") or e.get("path"), e["source"]) for e in tb["never_ai_approve"] + tb.get("content_rules", []) if e.get("source")]

    def rules(self):
        return cfg.load_rules(lambda p: self.read(p) if self.exists(p) else None)


class CodeownersSnapshotTest(SecScanBase):
    def test_init_snapshots_security_owner_lines_only(self):
        self.put(".github/CODEOWNERS", CODEOWNERS)
        rc, out, _ = self.init()
        self.assertEqual(rc, 0, out)
        self.assertEqual(self.sourced(), [("infra/**", "codeowners:3"), ("**/docs/**", "codeowners:4"), ("payments/*", "codeowners:5")])
        loaded = self.rules()
        self.assertEqual(loaded.problems, [])
        self.assertIn(("glob", "infra/**"), {secpaths.key(e) for e in loaded.value["trust_boundary"]["never_ai_approve"]})
        line = next(ln for ln in out.splitlines() if "has 6 owned lines" in ln)
        self.assertIn("3 name a security owner", line)
        self.assertIn("line 7 !/x (pattern not convertible)", line)
        self.assertEqual(self.read("code_review.md").count("%%agentive"), 0)       # no placeholder is left in the installed file
        self.assert_contracts()

    def test_no_codeowners_file_gives_the_floor_and_a_hint(self):
        rc, out, _ = self.init()
        self.assertEqual(rc, 0, out)
        self.assertEqual(self.sourced(), [])
        self.assertTrue(any("no CODEOWNERS file" in ln for ln in out.splitlines()))
        self.assertEqual(self.rules().problems, [])

    def test_update_proposes_the_difference_and_keeps_project_entries(self):
        self.put(".github/CODEOWNERS", CODEOWNERS)
        self.init()
        self.put(".agents/custom/code_review.md", "```yaml agentive-review-rules\nagentive-review-rules:\n  trust_boundary:\n    add:\n"
                 "      - {glob: \"services/ledger/**\", reason: billing}\n    remove:\n      - {glob: \"**/billing/**\", reason: \"billing/ is the report renderer\"}\n```\n")
        self.git("add", "-A")
        self.git("commit", "-qm", "init")
        self.put(".github/CODEOWNERS", CODEOWNERS.replace("docs/ @org/appsec", "docs/guide/ @org/appsec") + "/ops/ @iam-team\n")
        rc, out, _ = self.engine("update")
        self.assertEqual(rc, 0, out)
        change = next(ln for ln in out.splitlines() if "security paths changed since the last run" in ln)
        self.assertIn("written to code_review.md#rules", change)
        self.assertIn("added docs/guide/** (codeowners:4), ops/** (codeowners:8)", change)
        self.assertIn("no longer declared **/docs/** (codeowners:4)", change)
        self.assertEqual([g for g, _ in self.sourced()], ["infra/**", "docs/guide/**", "payments/*", "ops/**"])
        # the project's own entries are still in the block, and the merged block loads: add and remove are in force
        tb = self.rules().value["trust_boundary"]
        keys = {secpaths.key(e) for e in tb["never_ai_approve"]}
        self.assertIn(("glob", "services/ledger/**"), keys)
        self.assertNotIn(("glob", "**/billing/**"), keys)
        self.assertEqual(tb["removed"][0]["reason"], "billing/ is the report renderer")
        # a second update changes nothing and reports no difference
        rc, out, changed = self.engine("update")
        self.assertEqual((rc, changed), (0, []), out)
        self.assertFalse(any("changed since the last run" in ln for ln in out.splitlines()))

    def test_update_never_drops_hand_edits_silently(self):
        self.put(".github/CODEOWNERS", CODEOWNERS)
        self.init()
        text = self.read("code_review.md").replace('      - {glob: "**/SECURITY.md", reason: security}\n',
                                                    '      - {glob: "**/SECURITY.md", reason: security}\n      - {glob: "legacy/vault/**", reason: secrets}\n', 1)
        self.put("code_review.md", text)
        self.git("add", "-A")
        self.git("commit", "-qm", "init")
        self.put(".github/CODEOWNERS", CODEOWNERS + "/ops/ @iam-team\n")
        rc, out, _ = self.engine("update")
        self.assertEqual(rc, 0, out)
        self.assertIn("legacy/vault/**", self.read("code_review.md"))               # the customized region is not overwritten
        self.assertTrue(any(ln.startswith("AGENTIVE_DRIFT id=code_review.md#rules") for ln in out.splitlines()), out)
        change = next(ln for ln in out.splitlines() if "security paths changed since the last run" in ln)
        self.assertIn("NOT written", change)
        self.assertIn("ops/**", change)
        mine = next(ln for ln in out.splitlines() if "entries in code_review.md#rules are yours" in ln)
        self.assertIn("legacy/vault/**", mine)
        self.assertIn("trust_boundary.add", mine)

    def test_codeowners_over_3mb_is_not_read(self):
        self.put(".github/CODEOWNERS", "/x/ @org/security\n" + "#" * (3 * 1024 * 1024))
        rc, out, _ = self.init()
        self.assertEqual(rc, 0, out)
        self.assertEqual(self.sourced(), [])
        self.assertTrue(any("larger than 3 MB" in ln for ln in out.splitlines()))


class PresetTest(SecScanBase):
    def next_entries(self, spec):
        self.put("package.json", json.dumps({"name": "x", "dependencies": {"next": spec}}))
        got = secscan.preset_nextjs(self.repo)
        return sorted(e["glob"] for e in got["entries"])

    def test_next_proxy_for_16_and_middleware_below(self):
        self.assertEqual(self.next_entries("^16.0.1"), ["proxy.js", "proxy.ts", "src/proxy.js", "src/proxy.ts"])
        self.assertEqual(self.next_entries("16.0.0"), ["proxy.js", "proxy.ts", "src/proxy.js", "src/proxy.ts"])
        self.assertEqual(self.next_entries("~15.5.2"), ["middleware.js", "middleware.ts", "src/middleware.js", "src/middleware.ts"])
        self.assertEqual(self.next_entries(">=14"), ["middleware.js", "middleware.ts", "src/middleware.js", "src/middleware.ts"])
        both = self.next_entries("latest")                                     # no version named: both names, more review never less
        self.assertEqual(len(both), 8)
        self.assertIsNone((self.put("package.json", json.dumps({"dependencies": {"react": "19"}})), secscan.preset_nextjs(self.repo))[1])
        self.assertEqual((secscan.next_major("^16.0.1"), secscan.next_major("canary"), secscan.next_major("workspace:*")), (16, None, None))

    def test_next_version_switch_reaches_the_installed_block_on_update(self):
        self.put("package.json", json.dumps({"name": "x", "dependencies": {"next": "15.2.0"}}))
        self.init()
        self.assertEqual(sorted(g for g, _ in self.sourced()), ["middleware.js", "middleware.ts", "src/middleware.js", "src/middleware.ts"])
        self.git("add", "-A")
        self.git("commit", "-qm", "init")
        self.put("package.json", json.dumps({"name": "x", "dependencies": {"next": "16.0.3"}}))
        rc, out, _ = self.engine("update")
        self.assertEqual(rc, 0, out)
        self.assertEqual(sorted(g for g, _ in self.sourced()), ["proxy.js", "proxy.ts", "src/proxy.js", "src/proxy.ts"])
        self.assertTrue(any("added proxy.ts (preset:nextjs)" in ln and "no longer declared middleware.ts" in ln for ln in out.splitlines()), out)

    def test_spring_and_django_content_presets(self):
        self.put("pom.xml", "<project><dependencies><dependency><artifactId>spring-boot-starter-security</artifactId></dependency></dependencies></project>")
        self.put("requirements.txt", "Django==5.1\ndjango-cors-headers==4.0\n")
        rc, out, _ = self.init()
        self.assertEqual(rc, 0, out)
        content = self.block()["trust_boundary"]["content_rules"]
        by = {e["source"] + ":" + e["path"]: e["contains"] for e in content}
        self.assertEqual(by["preset:spring:**/*.java"], ["@EnableWebSecurity", "SecurityFilterChain"])
        self.assertEqual(by["preset:spring:**/*.kt"], ["@EnableWebSecurity", "SecurityFilterChain"])
        self.assertEqual(by["preset:django:**/settings*.py"], ["SECRET_KEY", "DEBUG", "ALLOWED_HOSTS", "_COOKIE_SECURE"])
        self.assertEqual(self.rules().problems, [])
        self.assertTrue(any("stack presets spring" in ln and "django" in ln for ln in out.splitlines()))

    def test_django_dependency_needs_the_package_itself(self):
        self.put("requirements.txt", "django-cors-headers==4.0\ndjangorestframework==3.15\n")
        self.assertIsNone(secscan.preset_django(self.repo))
        self.put("pyproject.toml", '[project]\ndependencies = ["django>=5"]\n')
        self.assertEqual(secscan.preset_django(self.repo)["id"], "django")

    def test_no_k8s_or_terraform_preset(self):
        """K8s RBAC kinds and Terraform IAM resources are deferred (미검증): nothing is written for them."""
        self.put("main.tf", 'resource "aws_iam_role" "r" {}\n')
        self.put("k8s/role.yaml", "kind: ClusterRole\n")
        self.assertEqual(secscan.presets(self.repo), [])


class SecurityToolTest(SecScanBase):
    def test_tools_are_detected_from_config_files_and_workflows_and_recorded_in_the_manifest(self):
        self.put(".gitleaks.toml", "title = 'x'\n")
        self.put(".github/workflows/codeql.yml", "jobs:\n  a:\n    steps:\n      - uses: github/codeql-action/analyze@v3\n")
        self.put(".github/workflows/scan.yml", "jobs:\n  a:\n    steps:\n      - run: semgrep ci\n")
        rc, out, _ = self.init()
        self.assertEqual(rc, 0, out)
        self.assertEqual(self.manifest()["security_checks"],
                         [{"tool": "gitleaks", "config": ".gitleaks.toml"}, {"tool": "semgrep", "config": ".github/workflows/scan.yml"},
                          {"tool": "codeql", "config": ".github/workflows/codeql.yml"}])
        line = next(ln for ln in out.splitlines() if "security tools found" in ln)
        self.assertIn("their rules are not copied", line)
        self.assert_contracts()
        # the tool's own rules are not copied into the review rules block
        self.assertNotIn("gitleaks", self.read("code_review.md").lower().split("agentive-review-rules:")[1])

    def test_no_tool_no_key(self):
        self.init()
        self.assertNotIn("security_checks", self.manifest())


if __name__ == "__main__":
    unittest.main()
