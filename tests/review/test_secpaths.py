# agentive-unit: selftest.component
# design: decisions.accepted[SECPATHS], spec.review_pipeline.stages[RV2], spec.review_pipeline.stages[RV4].raise_only_check, docs/impl/decisions-log.md §12
"""SECPATHS (2026-10-29): security-sensitive change detection. Case-sensitive matching that is directory and segment aware,
the override grammar (extend_defaults, add, remove with a required reason) that MERGES with the shipped floor, content rules
{path, contains}, shadow rules, security tool checks, the CODEOWNERS reader and the RV4 raised_by record."""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import helpers as h  # noqa: E402
from test_pipeline import PipelineBase, body  # noqa: E402

from agentive.review import config as cfg, rv2, rv4, secpaths  # noqa: E402

REPORT = "https://fw.local/schemas/review-report.schema.json#/properties/"


class FakeDiff:
    def __init__(self, head=None, base=None):
        self.head, self.base = head or {}, base or {}

    def show_head(self, p):
        return self.head.get(p, "")

    def show_base(self, p):
        return self.base.get(p, "")


def block(trust_boundary_extra="", managed="", content=""):
    """A code_review.md whose rules block is the shipped template plus `trust_boundary_extra` (indented 4 spaces, as a custom
    override merged into the block would be)."""
    text = h.rules_region_text(managed, content)
    if trust_boundary_extra:
        text = text.replace("    extend_defaults: true\n", "    extend_defaults: true\n" + trust_boundary_extra, 1)
    return text


def load(text):
    return cfg.load_rules(lambda p: text)


def detect(rules, files, hunks=None, manifest=None, checks=None):
    statuses = [("M", p, None) for p in files]
    got, always = rv2.run(FakeDiff(), statuses, hunks or {}, rules, manifest or {}, {"env_vars_changed": [], "agentive_check_tags": {}},
                          pr=None, checks=checks)
    return got, always


def hit_paths(got):
    return {x["path"] for x in got["hits"]}


def added(*lines):
    return {"added": [(i, ln) for i, ln in enumerate(lines, 1)], "removed": []}


class MatchingTest(unittest.TestCase):
    """L2: case-sensitive, directory aware, segment tokens."""
    R = load(block()).value

    def paths(self, *files):
        got, _ = detect(self.R, files)
        return hit_paths(got)

    def test_directory_segments_are_matched(self):
        # V2-18: `**/*secret*` and `**/permissions*` looked at the last path element only
        self.assertEqual(self.paths("secrets/prod.yaml", "src/permissions/x.py", "cfg/my-secret/a.yaml"),
                         {"secrets/prod.yaml", "src/permissions/x.py", "cfg/my-secret/a.yaml"})
        self.assertTrue(secpaths.glob_dir_match("**/*secret*", "secrets/prod.yaml"))
        self.assertTrue(secpaths.glob_dir_match("**/permissions*", "src/permissions/x.py"))
        self.assertFalse(secpaths.glob_dir_match("**/permissions*", "src/permission/x.py"))
        self.assertFalse(secpaths.glob_dir_match("src/**", "other/src/a"))

    def test_matching_is_case_sensitive(self):
        self.assertEqual(self.paths("src/Auth/x.py", "Secrets/prod.yaml", "src/PERMISSIONS/x.py"), set())
        self.assertEqual(self.paths("SECURITY.md", "docs/SECURITY.md", ".github/SECURITY.md"), {"SECURITY.md", "docs/SECURITY.md", ".github/SECURITY.md"})
        self.assertEqual(self.paths("security.md", "Security.md"), {"security.md"})      # SECURITY.md is listed by its conventional casing; the token `security` matches the lower-case name only
        self.assertFalse(secpaths.token_match("auth", "src/Auth/x.py"))
        self.assertFalse(secpaths.glob_dir_match("**/auth/**", "src/Auth/x.py"))

    def test_token_match_is_per_segment_word(self):
        self.assertEqual(self.paths("src/author.py", "authors/list.py", "docs/authority/x.txt"), set())
        self.assertEqual(self.paths("src/auth_service.py", "web/login-form.tsx", "lib/jwt.helpers.ts", "api/oauth2/cb.go"),
                         {"src/auth_service.py", "web/login-form.tsx", "lib/jwt.helpers.ts", "api/oauth2/cb.go"})
        self.assertTrue(secpaths.token_match("auth", "auth/x"))
        self.assertTrue(secpaths.token_match("auth", "a/b/auth.py"))
        self.assertFalse(secpaths.token_match("auth", "a/authentic/x"))
        self.assertFalse(secpaths.token_match("iam", "src/william.py"))

    def test_hit_records_layer_and_reason(self):
        got, _ = detect(self.R, ["src/auth_service.py", "secrets/prod.yaml", "SECURITY.md", ".github/workflows/ci.yml", "README.md"])
        by = {x["path"]: x for x in got["hits"]}
        self.assertEqual((by["src/auth_service.py"]["layer"], by["src/auth_service.py"]["reason"], by["src/auth_service.py"]["glob"]),
                         ("default", "auth", "token:auth"))
        self.assertEqual(by["secrets/prod.yaml"]["layer"], "default")
        self.assertEqual(by["SECURITY.md"]["reason"], "security")
        self.assertNotIn("README.md", by)
        self.assertEqual(h.validate_ref(REPORT + "rv2", got), [])

    def test_codeowners_file_is_protected_check_config(self):
        got, always = detect(self.R, [".github/CODEOWNERS", "CODEOWNERS", "docs/CODEOWNERS"])
        self.assertTrue(all(x["reason"] == "check_config" and x["layer"] == "protected" for x in got["hits"]))
        self.assertEqual(hit_paths(got), {".github/CODEOWNERS", "CODEOWNERS", "docs/CODEOWNERS"})
        self.assertTrue(any(code == "always_human" for code, _ in always))
        # not removable: the protected list is built in
        r = load(block("    remove:\n      - {glob: \".github/CODEOWNERS\", reason: test}\n")).value
        got, _ = detect(r, [".github/CODEOWNERS"])
        self.assertEqual(hit_paths(got), {".github/CODEOWNERS"})


class OverrideGrammarTest(unittest.TestCase):
    def test_block_merges_with_the_floor_instead_of_replacing_it(self):
        mini = ("agentive-review-rules:\n  trust_boundary:\n    never_ai_approve:\n      - {glob: \"services/x/**\", reason: other}\n"
                "    confirm_scope_globs:\n      hooks: [\"a/**\"]\n      permissions: [\"b/**\"]\n      ci: [\"c/**\"]\n      claude_settings: [\"d/**\"]\n"
                "      codex_dir: [\"e/**\"]\n      review_config: [\"f/**\"]\n  tiers:\n    tier0_classes: [docs]\n    tier0_max_changed_lines: 10\n"
                "    large_change_lines: 100\n    confirm_scope_without_human_tier: 2\n")
        got = load("```yaml agentive-review-rules\n" + mini + "```\n")
        self.assertEqual(got.problems, [])
        tb = got.value["trust_boundary"]
        keys = {secpaths.key(e) for e in tb["never_ai_approve"]}
        self.assertIn(("glob", "services/x/**"), keys)
        self.assertTrue(cfg.floor_keys() <= keys)                      # the floor is still there: it used to be replaced
        self.assertIn(".github/workflows/**", tb["confirm_scope_globs"]["ci"])
        self.assertIn("c/**", tb["confirm_scope_globs"]["ci"])
        off = load("```yaml agentive-review-rules\n" + mini.replace("  trust_boundary:\n", "  trust_boundary:\n    extend_defaults: false\n") + "```\n")
        self.assertEqual(off.problems, [])
        self.assertEqual([secpaths.key(e) for e in off.value["trust_boundary"]["never_ai_approve"]], [("glob", "services/x/**")])
        self.assertEqual(off.value["trust_boundary"]["confirm_scope_globs"]["ci"], ["c/**"])

    def test_add_and_remove(self):
        extra = ("    add:\n      - {glob: \"services/ledger/**\", reason: billing}\n      - {token: ledger, reason: billing}\n"
                 "      - {path: \"**/*.tf\", contains: aws_iam_, reason: permissions}\n"
                 "    remove:\n      - {glob: \"**/billing/**\", reason: \"billing/ is the report renderer here\"}\n"
                 "      - {token: billing, reason: \"billing/ is the report renderer here\"}\n"
                 "      - {token: login, reason: \"login is a marketing page here\"}\n")
        got = load(block(extra))
        self.assertEqual(got.problems, [])
        tb = got.value["trust_boundary"]
        keys = {secpaths.key(e) for e in tb["never_ai_approve"]}
        self.assertIn(("glob", "services/ledger/**"), keys)
        self.assertIn(("token", "ledger"), keys)
        self.assertNotIn(("glob", "**/billing/**"), keys)
        self.assertNotIn(("token", "billing"), keys)                  # a removal is exact: the glob and the token are two entries
        self.assertNotIn(("token", "login"), keys)
        self.assertIn(("token", "auth"), keys)                       # other tokens of the same group stay
        self.assertEqual([secpaths.key(e) for e in tb["content_rules"]], [("content", "**/*.tf", ("aws_iam_",))])
        self.assertEqual({e["reason"] for e in tb["removed"]}, {"billing/ is the report renderer here", "login is a marketing page here"})
        self.assertEqual(len(tb["removed"]), 3)
        got2, _ = detect(got.value, ["src/billing/x.py", "web/login.tsx", "services/ledger/a.go", "src/ledger.go", "src/auth.py"])
        self.assertEqual(hit_paths(got2), {"services/ledger/a.go", "src/ledger.go", "src/auth.py"})
        self.assertEqual([r["reason"] for r in got2["removed"]][0], "billing/ is the report renderer here")
        self.assertEqual(h.validate_ref(REPORT + "rv2", got2), [])
        # the user's add entry is org layer; floor entries are default layer
        by = {x["path"]: x for x in got2["hits"]}
        self.assertEqual((by["services/ledger/a.go"]["layer"], by["services/ledger/a.go"]["source"]), ("org", "add"))

    def test_remove_requires_a_reason(self):
        for bad in ("    remove:\n      - {glob: \"**/billing/**\"}\n", "    remove:\n      - {glob: \"**/billing/**\", reason: \"\"}\n",
                    "    remove:\n      - {glob: \"**/billing/**\", token: billing, reason: x}\n",
                    "    remove:\n      - {path: \"**/*.tf\", reason: x}\n"):
            got = load(block(bad))
            self.assertTrue(got.problems, bad)
            self.assertEqual(got.value, cfg.DEFAULT_RULES)            # invalid -> the design defaults (fail-closed), RV4 adds always_human
        # and the same through the pipeline: the problem becomes a tier-2 reason
        got = load(block("    remove:\n      - {glob: \"**/billing/**\"}\n"))
        _, always = rv2.run(FakeDiff(), [("M", "README.md", None)], {}, got.value, {}, {"env_vars_changed": [], "agentive_check_tags": {}},
                            rules_problems=got.problems)
        self.assertTrue(any("review rules invalid" in d for _, d in always))

    def test_schema_matches_the_loader(self):
        v = h.schema_validator("review-rules")
        good = "    add:\n      - {glob: \"a/**\", reason: auth}\n    remove:\n      - {glob: \"**/billing/**\", reason: why}\n"
        doc = cfg.ayaml.loads(__import__("agentive.review.util", fromlist=["x"]).fenced_blocks(block(good), "yaml agentive-review-rules")[0])
        self.assertEqual(v(doc), [])
        for bad in ("    remove:\n      - {glob: \"**/billing/**\"}\n", "    add:\n      - {glob: \"a/**\"}\n",
                    "    add:\n      - {path: \"a/**\", reason: auth}\n", "    extend_defaults: maybe\n"):
            text = block(bad).replace("    extend_defaults: true\n", "", 1) if "extend_defaults" in bad else block(bad)
            d = cfg.ayaml.loads(__import__("agentive.review.util", fromlist=["x"]).fenced_blocks(text, "yaml agentive-review-rules")[0])
            self.assertTrue(v(d), bad)
        self.assertEqual(v({"agentive-review-rules": cfg.DEFAULT_RULES}), [])

    def test_shadow_rules_report_but_force_nothing(self):
        extra = "    add:\n      - {glob: \"experimental/**\", reason: security, shadow: true}\n"
        r = load(block(extra)).value
        got, _ = detect(r, ["experimental/a.py"])
        self.assertEqual(got["hits"], [])
        self.assertEqual([x["path"] for x in got["shadow_hits"]], ["experimental/a.py"])
        self.assertEqual(h.validate_ref(REPORT + "rv2", got), [])
        # a second, enforced entry with the same key wins over the shadow one
        self.assertEqual(secpaths.dedupe([{"glob": "x", "reason": "other", "shadow": True}, {"glob": "x", "reason": "other"}]),
                         [{"glob": "x", "reason": "other"}])


class ContentRulesTest(unittest.TestCase):
    """L3: {path, contains} rules and the stack presets the engine writes."""

    def test_content_rule_matches_added_and_removed_lines_of_the_file(self):
        extra = "    add:\n      - {path: \"**/*.tf\", contains: aws_iam_, reason: permissions}\n"
        r = load(block(extra)).value
        got, _ = detect(r, ["infra/main.tf"], {"infra/main.tf": added('resource "aws_iam_role" "r" {')})
        self.assertEqual([(x["path"], x["reason"], x["layer"], x["contains"]) for x in got["hits"]], [("infra/main.tf", "permissions", "content", "aws_iam_")])
        got, _ = detect(r, ["infra/main.tf"], {"infra/main.tf": {"added": [], "removed": [(3, 'resource "aws_iam_policy" "p" {')]}})
        self.assertEqual(len(got["hits"]), 1)                                              # a removed line counts too
        got, _ = detect(r, ["infra/main.tf"], {"infra/main.tf": added('resource "aws_s3_bucket" "b" {')})
        self.assertEqual(got["hits"], [])
        got, _ = detect(r, ["infra/main.go"], {"infra/main.go": added('aws_iam_')})
        self.assertEqual(got["hits"], [])                                                  # the path must match too
        got, _ = detect(r, ["infra/main.tf"], {"infra/main.tf": added('AWS_IAM_ROLE')})
        self.assertEqual(got["hits"], [])                                                  # case-sensitive substring

    def test_utils_helpers_with_a_jwt_check_is_found_by_content_not_by_name(self):
        extra = "    add:\n      - {path: \"**/*.py\", contains: [\"jwt.decode\", \"verify_signature\"], reason: auth}\n"
        r = load(block(extra)).value
        got, _ = detect(r, ["utils/helpers.py"], {"utils/helpers.py": added("payload = jwt.decode(token, key)")})
        self.assertEqual([x["path"] for x in got["hits"]], ["utils/helpers.py"])
        self.assertEqual(h.validate_ref(REPORT + "rv2", got), [])

    def test_stack_preset_entries_from_the_block(self):
        content = ("content_rules:\n  - {path: \"**/*.java\", contains: [\"@EnableWebSecurity\", \"SecurityFilterChain\"], reason: auth, "
                   "source: \"preset:spring\"}\n  - {path: \"**/settings*.py\", contains: [\"SECRET_KEY\", \"DEBUG\", \"ALLOWED_HOSTS\", \"_COOKIE_SECURE\"], "
                   "reason: security, source: \"preset:django\"}")
        r = load(block(managed="- {glob: \"proxy.ts\", reason: auth, source: \"preset:nextjs\"}", content=content))
        self.assertEqual(r.problems, [])
        r = r.value
        got, _ = detect(r, ["src/main/java/app/WebConfig.java", "mysite/settings.py", "mysite/urls.py", "proxy.ts", "middleware.ts"],
                        {"src/main/java/app/WebConfig.java": added("@Bean", "SecurityFilterChain chain(HttpSecurity http) {"),
                         "mysite/settings.py": added("CSRF_COOKIE_SECURE = False"), "mysite/urls.py": added("DEBUG = True"),
                         "proxy.ts": added("export function proxy() {}")})
        by = {x["path"]: x for x in got["hits"]}
        self.assertEqual(set(by), {"src/main/java/app/WebConfig.java", "mysite/settings.py", "proxy.ts"})
        self.assertEqual((by["proxy.ts"]["layer"], by["proxy.ts"]["source"]), ("content", "preset:nextjs"))
        self.assertEqual(by["mysite/settings.py"]["source"], "preset:django")
        self.assertEqual(by["src/main/java/app/WebConfig.java"]["contains"], "@EnableWebSecurity, SecurityFilterChain")


class SecurityToolChecksTest(unittest.TestCase):
    R = load(block()).value
    M = {"security_checks": [{"tool": "gitleaks", "config": ".gitleaks.toml"}, {"tool": "codeql", "config": ".github/workflows/codeql.yml"}]}

    def test_failed_tool_check_is_an_always_human_item(self):
        got, always = detect(self.R, ["README.md"], manifest=self.M, checks=[{"name": "Gitleaks scan", "status": "fail"}, {"name": "CodeQL", "status": "pass"}])
        self.assertEqual([(c["tool"], c["status"]) for c in got["security_checks"]], [("gitleaks", "fail"), ("codeql", "pass")])
        self.assertTrue(any(c == "always_human" and "Gitleaks scan" in d for c, d in always))
        self.assertEqual(h.validate_ref(REPORT + "rv2", got), [])

    def test_missing_check_is_recorded_not_a_tier_reason(self):
        got, always = detect(self.R, ["README.md"], manifest=self.M, checks=[{"name": "CodeQL", "status": "pass"}])
        self.assertEqual([(c["tool"], c["status"]) for c in got["security_checks"]], [("gitleaks", "not_run"), ("codeql", "pass")])
        self.assertEqual(always, [])
        got, _ = detect(self.R, ["README.md"], manifest={}, checks=[])
        self.assertNotIn("security_checks", got)


class CodeownersTest(unittest.TestCase):
    TEXT = ("# owners\n*.js @org/frontend\n/infra/ @org/security-team @bob\ndocs/ @org/appsec\n/payments/* @alice @org/sre\n"
            "apps/ @octo\n!/x @org/security\n/a/b.py @william\n/orphan/\nsecrets/** securityteam@example.com # trailing comment\n"
            "/keys/[ab].pem @org/iam\n")

    def test_only_security_owners_become_entries_with_the_line_number(self):
        got = secpaths.parse_codeowners(self.TEXT)
        self.assertEqual([(e["glob"], e["source"]) for e in got["entries"]],
                         [("infra/**", "codeowners:3"), ("**/docs/**", "codeowners:4"), ("payments/*", "codeowners:5"),
                          ("secrets/**", "codeowners:10")])
        self.assertTrue(all(e["reason"] == "security" for e in got["entries"]))
        self.assertEqual([(n, p) for n, p, _ in got["skipped"]], [(7, "!/x"), (11, "/keys/[ab].pem")])
        self.assertEqual(got["owned"], 9)            # lines with an owner; /orphan/ has none

    def test_owner_name_heuristic_is_token_based(self):
        yes = ["@org/security-team", "@appsec", "@org/platform_sre", "@org/IAM", "securityteam@example.com", "@Org/InfoSec.Reviewers", "@privacy-office"]
        no = ["@william", "@liam", "@org/frontend", "@treasure", "@secure-coders", "@bob"]
        for o in yes:
            self.assertTrue(secpaths.owner_looks_security(o), o)
        for o in no:
            self.assertFalse(secpaths.owner_looks_security(o), o)

    def test_pattern_conversion_follows_the_github_description(self):
        cases = {"/docs/": "docs/**", "/docs": "docs", "docs/": "**/docs/**", "*.js": "**/*.js", "README.md": "**/README.md",
                 "a/b": "a/b", "/a/b/*.py": "a/b/*.py", "**/logs": "**/logs", "src/**": "src/**", "apps/": "**/apps/**",
                 "!x": None, "a[1].txt": None, "\\#file": None}
        for pat, want in cases.items():
            self.assertEqual(secpaths.codeowners_glob(pat), want, pat)
        # converted globs match what CODEOWNERS means
        self.assertTrue(secpaths.glob_dir_match("**/apps/**", "x/y/apps/main.go"))
        self.assertFalse(secpaths.glob_dir_match("docs/**", "sub/docs/a.md"))
        self.assertTrue(secpaths.glob_dir_match("**/*.js", "a/b/c.js"))


class Rv4RaisedByTest(unittest.TestCase):
    def test_llm_raise_records_raised_by_with_paths_and_reason(self):
        out = {"human_needed": True, "criteria": ["b"], "paths": ["src/pay.py", 3, "src/x.py"], "why": "touches the payout calculation"}
        rec = rv4.finish(1, ["tier1"], out, True)
        self.assertEqual((rec["tier"], rec["raised_by_llm"], rec["raised_by"]), (2, True, "rv4_llm"))
        self.assertEqual(rec["raised"], {"paths": ["src/pay.py", "src/x.py"], "reason": "touches the payout calculation", "criteria": ["b"]})
        self.assertEqual(h.validate_ref(REPORT + "rv4", rec), [])
        # no raise, no record; an invalid answer is tier 2 without a raised_by claim
        self.assertNotIn("raised_by", rv4.finish(1, ["tier1"], {"human_needed": False, "criteria": []}, True))
        self.assertNotIn("raised_by", rv4.finish(0, ["tier0"], {"oops": 1}, True))
        # paths are optional in the answer
        self.assertEqual(rv4.finish(1, ["tier1"], {"human_needed": True, "criteria": ["a"], "why": "w"}, True)["raised"]["paths"], [])

    def test_the_raise_only_prompt_and_task_format_ask_for_paths(self):
        from agentive.review import tasks
        self.assertIn("paths", tasks.OUTPUT_FORMATS["raise-only"])
        self.assertIn('"paths"', h.read(os.path.join(h.SRC, "dot-agents", "agents", "raise-only.md")))


class DistillLoopTest(unittest.TestCase):
    """The learning loop: a miss becomes the narrowest `add` rule in shadow mode, the `rv2` proof command shows it on the missed diff,
    and a false positive becomes a `remove` entry with a reason."""
    REVIEW = os.path.join(h.SRC, "dot-agents", "bin", "agentive-review")

    def setUp(self):
        self.repo = h.Repo({"code_review.md": block(), "README.md": "# r\n"})
        self.base = self.repo.commit("base", date="2026-01-01T00:00:00Z")
        self.repo.write({"services/ledger/payout.py": "def payout(x):\n    return x * 2\n", "src/billing/report.py": "x = 1\n"})
        self.head = self.repo.commit("change", date="2026-01-02T00:00:00Z")

    def tearDown(self):
        self.repo.cleanup()

    def rv2(self):
        rc, out = self.repo.run(".agents/bin/agentive-review", "rv2", "--base", self.base, "--head", self.head)
        self.assertEqual(rc, 0, out)
        return out

    def test_missed_path_is_found_by_a_shadow_rule_and_promoted_by_removing_the_flag(self):
        before = self.rv2()
        self.assertNotIn("services/ledger/payout.py", before)                       # the miss
        self.assertIn("AGENTIVE_RV2_HIT path=src/billing/report.py layer=default", before)
        shadow = "    add:\n      - {glob: \"services/ledger/**\", reason: billing, shadow: true}\n"
        self.repo.write({"code_review.md": block(shadow)})
        out = self.rv2()
        self.assertIn("AGENTIVE_RV2_SHADOW path=services/ledger/payout.py layer=org glob=services/ledger/** reason=billing source=add", out)
        self.assertIn("AGENTIVE_RV2=done hits=1 shadow_hits=1", out)                 # report only: the tier decision is unchanged
        self.repo.write({"code_review.md": block(shadow.replace(", shadow: true", ""))})
        self.assertIn("AGENTIVE_RV2_HIT path=services/ledger/payout.py layer=org", self.rv2())

    def test_false_positive_is_removed_with_a_reason_and_the_other_hits_stay(self):
        rm = ("    remove:\n      - {glob: \"**/billing/**\", reason: \"billing/ is the report renderer\"}\n"
              "      - {token: billing, reason: \"billing/ is the report renderer\"}\n")
        self.repo.write({"code_review.md": block(rm + "    add:\n      - {glob: \"services/ledger/**\", reason: billing}\n")})
        out = self.rv2()
        self.assertNotIn("src/billing/report.py", out)
        self.assertIn("path=services/ledger/payout.py", out)
        self.repo.write({"code_review.md": block("    remove:\n      - {glob: \"**/billing/**\"}\n")})
        out = self.rv2()
        self.assertIn("AGENTIVE_RV2_PROBLEM", out)                                   # no reason: the block is invalid
        self.assertIn("needs a reason", out)

    def test_distill_skill_describes_the_loop(self):
        text = h.read(os.path.join(h.SRC, "dot-agents", "skills", "distill", "SKILL.md"))
        for must in ("missed_by: rv2_trust_boundary", "raised_by: rv4_llm", "shadow: true", "`remove` entry", "reason` is required",
                     "agentive-review rv2 --base", "evals/reviewer/<id>.yaml", ".agents/custom/code_review.md", "narrowest"):
            self.assertIn(must, text)
        self.assertLessEqual(len(text.splitlines()), 500)


class EndToEndTest(PipelineBase):
    """The CI profile on a real diff: layer, source and the L4 record reach review-report.json and are schema valid."""

    def test_floor_hit_and_llm_raise_reach_the_report(self):
        self.repo.write({"secrets/prod.yaml": "a: 1\n", "src/author.py": "x = 1\n"})
        head = self.repo.commit("secrets")
        rep, _, _ = self.ci(head, h.default_responder(), body())
        self.assertEqual([(x["path"], x["layer"]) for x in rep["rv2"]["hits"]], [("secrets/prod.yaml", "default")])
        self.assertEqual(rep["rv4"]["tier"], 2)

    def test_llm_raise_reaches_the_report(self):
        self.repo.write({"src/pay.py": "def payout(x):\n    return x * 2\n"})
        head = self.repo.commit("pay")
        responder = h.default_responder(raise_needed=True)

        def with_paths(agent, inp):
            r = responder(agent, inp)
            if agent == "raise-only":
                r = dict(r, paths=["src/pay.py"])
            return r
        rep, _, _ = self.ci(head, with_paths, body(), work="w2")
        self.assertEqual((rep["rv4"]["tier"], rep["rv4"]["raised_by"], rep["rv4"]["raised"]["paths"]), (2, "rv4_llm", ["src/pay.py"]))


if __name__ == "__main__":
    unittest.main()
