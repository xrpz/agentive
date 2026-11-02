# agentive-unit: selftest.component
# design: decisions.accepted[PRLABEL], spec.work_loop.pr_skill, spec.maintenance.janitor.label, decisions.accepted[HUMANMERGE], docs/impl/decisions-log.md §26
"""E2E-40 (PRLABEL, 2026-11-01): in headless runs (`claude -p`) /pr ends with `AGENTIVE_PR=done ... NEXT=report the PR URL and end the run`,
so a step after /pr (the janitor's label) may never run. The label is now part of publication: `pr-body.sh publish --label <name>`
adds it and reads it back before the final line; a missing label is an AGENTIVE_FAIL line with a NEXT; no skill has a work step
after /pr."""
import re
import unittest

from helpers import SKILLS, Sandbox, git, read, write
from test_prbody import CODE_REVIEW

FULL = ("## Verification\n\nHEAD: abc\n- lint: ok\n\n## Risk\n\nnone\n\n## How to revert\n\nrevert\n\n"
        "```yaml agentive-review\nagentive-review:\n  implementer:\n    tool: claude_code\n    model: m\n"
        "    session_id: s\n    started_at: \"2026-10-28T00:00:00Z\"\n  reviews: []\n```\n")
LABEL = "agentive-janitor:lint-fix"


class PublishLabel(unittest.TestCase):
    def setUp(self):
        self.sb = Sandbox({"README.md": "# demo\n", "code_review.md": CODE_REVIEW})
        git(self.sb.repo, "switch", "-q", "-c", "janitor/lint-fix-20261101")
        write(self.sb.repo, "src/auth/login.py", "LIMIT = 5\n")        # a trust-boundary path: tier 2, a human merges
        git(self.sb.repo, "add", "-A")
        git(self.sb.repo, "commit", "-q", "-m", "auth")
        git(self.sb.repo, "push", "-q", "-u", "origin", "janitor/lint-fix-20261101")
        write(self.sb.repo, ".agents/state/body.md", FULL)

    def tearDown(self):
        self.sb.close()

    def publish(self, routes, *extra, headless=True):
        env = self.sb.fake_gh(routes)
        if headless:
            env = dict(env, AGENTIVE_HEADLESS="1")
        return self.sb.run(["sh", self.sb.script("pr", "pr-body.sh"), "publish", ".agents/state/body.md", *extra], env=env)

    def create_routes(self, labels):
        return [(r"pulls\?head=", {"stdout": []}),
                (r"-X POST .*issues/21/labels", {"stdout": ""}),
                (r"-X GET .*issues/21/labels", labels),
                (r"-X POST .*pulls", {"stdout": {"number": 21, "html_url": "https://x/21"}})]

    def calls(self):
        return [" ".join(c["args"]) for c in self.sb.gh_calls()]

    def test_e2e_40_label_is_applied_and_verified_before_the_final_line(self):
        p = self.publish(self.create_routes({"stdout": f"bug\n{LABEL}\n"}), "--title", "chore: lint", "--label", LABEL)
        self.assertEqual(p.returncode, 0, p.stdout)
        lines = p.stdout.strip().splitlines()
        self.assertEqual(lines[0], "PR=21 URL=https://x/21 CREATED")
        self.assertEqual(lines[1], f"LABELED {LABEL} pr=21")
        self.assertTrue(lines[2].startswith("MERGE=human pr=21"), lines)
        self.assertTrue(lines[-1].startswith(f"AGENTIVE_PR=done pr=21 waiting=human_merge url=https://x/21 labels={LABEL}; NEXT=report the PR URL and end the run"), lines)
        c = self.calls()
        order = [next(i for i, x in enumerate(c) if re.search(pat, x)) for pat in (r"-X POST .*pulls\b(?!/)", r"-X POST .*issues/21/labels", r"-X GET .*issues/21/labels")]
        self.assertEqual(order, sorted(order), c)                         # created, labeled, read back
        post = [x for x in self.sb.gh_calls() if "POST" in x["args"] and any("labels" in a for a in x["args"])]
        self.assertEqual(len(post), 1)
        self.assertIn(f"labels[]={LABEL}", post[0]["args"])
        self.assertTrue(all(x["args"][:1] != ["pr"] for x in self.sb.gh_calls()))

    def test_e2e_40_label_applies_to_a_refresh_and_a_repeated_label_once(self):
        routes = [(r"pulls\?head=", {"stdout": [{"number": 21}]}), (r"-X PATCH", {"stdout": {"number": 21}}),
                  (r"pulls/21$", {"stdout": {"number": 21, "html_url": "https://x/21", "body": "## Verification\n\nold\n"}}),
                  (r"-X POST .*issues/21/labels", {"stdout": ""}), (r"-X GET .*issues/21/labels", {"stdout": f"{LABEL}\nextra\n"})]
        write(self.sb.repo, ".agents/state/body.md", "## Verification\n\nHEAD: abc\n")      # a MODE=refresh draft (E2E-50: a full body no longer refreshes)
        p = self.publish(routes, "--label", LABEL, "--label", LABEL, "--label", "extra")
        self.assertEqual(p.returncode, 0, p.stdout)
        self.assertIn(f"LABELED {LABEL},extra pr=21", p.stdout)
        self.assertEqual(len([c for c in self.calls() if "-X POST" in c and "labels" in c]), 2)

    def test_e2e_40_one_retry_when_the_first_read_back_misses(self):
        p = self.publish(self.create_routes({"seq": [{"stdout": "bug\n"}, {"stdout": f"{LABEL}\n"}]}), "--title", "t", "--label", LABEL)
        self.assertEqual(p.returncode, 0, p.stdout)
        self.assertIn(f"LABELED {LABEL} pr=21", p.stdout)
        self.assertEqual(len([c for c in self.calls() if "-X POST" in c and "labels" in c]), 2)

    def test_e2e_40_label_failure_is_reported_with_the_pr_and_a_next(self):
        p = self.publish(self.create_routes({"stdout": "bug\nagentive-janitor:lint-fix-2\n"}), "--title", "t", "--label", LABEL)
        self.assertEqual(p.returncode, 2)
        lines = p.stdout.strip().splitlines()
        self.assertEqual(lines[0], "PR=21 URL=https://x/21 CREATED")          # the PR exists: its number and URL stay in the output
        self.assertNotIn("LABELED", p.stdout)
        last = lines[-1]
        self.assertTrue(last.startswith(f"AGENTIVE_FAIL tool=pr-label reason=label_missing pr=21 url=https://x/21 labels=missing:{LABEL}; NEXT="), last)
        self.assertIn("label-pr.sh lint-fix 21", last)
        self.assertIn("the PR is not labeled", last)
        self.assertEqual(len([c for c in self.calls() if "-X POST" in c and "labels" in c]), 2)

    def test_e2e_40_label_post_error_is_a_missing_label_not_a_crash(self):
        routes = [(r"pulls\?head=", {"stdout": []}), (r"-X POST .*issues/21/labels", {"rc": 1}),
                  (r"-X GET .*issues/21/labels", {"rc": 1}),
                  (r"-X POST .*pulls", {"stdout": {"number": 21, "html_url": "https://x/21"}})]
        p = self.publish(routes, "--title", "t", "--label", LABEL)
        self.assertEqual(p.returncode, 2)
        self.assertTrue(p.stdout.strip().splitlines()[-1].startswith("AGENTIVE_FAIL tool=pr-label reason=label_missing"), p.stdout)

    def test_e2e_40_bad_label_stops_before_the_pr_is_created(self):
        p = self.publish(self.create_routes({"stdout": ""}), "--title", "t", "--label", "bad label;rm")
        self.assertEqual(p.returncode, 2)
        self.assertTrue(p.stdout.startswith("AGENTIVE_PR=stop reason=bad_label"), p.stdout)
        self.assertIn("NEXT=", p.stdout)
        self.assertEqual(self.sb.gh_calls(), [])

    def test_e2e_40_without_label_the_output_is_unchanged(self):
        p = self.publish(self.create_routes({"stdout": ""}), "--title", "t")
        self.assertEqual(p.returncode, 0, p.stdout)
        self.assertNotIn("LABELED", p.stdout)
        self.assertNotIn("labels=", p.stdout)
        self.assertEqual([c for c in self.calls() if "labels" in c], [])


class SkillsHaveNoStepAfterPr(unittest.TestCase):
    def test_e2e_40_janitor_passes_the_label_to_pr(self):
        t = read(SKILLS, "janitor", "SKILL.md")
        step = t[t.index("   4. Open the PR with /pr"):t.index("\n3. Report one line")]
        self.assertIn("--label agentive-janitor:<id>", step)
        self.assertNotRegex(step, r"then label it|label it at once")
        self.assertNotIn("label-pr.sh <id> <pr number>` adds", step)             # the script is only the repair named by a failed publish
        self.assertIn("nothing is left to do after /pr", step)
        self.assertEqual(len(re.findall(r"(?m)^   \d+\. ", t)), 4)               # step 4 is the last per-kind step: /pr
        self.assertNotIn("gh pr create", t)

    def test_e2e_40_pr_skill_documents_label(self):
        t = read(SKILLS, "pr", "SKILL.md")
        step6 = t[t.index("6. `sh .agents/skills/pr/scripts/pr-body.sh publish"):t.index("## --explain")]
        for words in ("--label <name>", "LABELED <names>", "AGENTIVE_FAIL tool=pr-label", "labels=<names>"):
            self.assertIn(words, step6)

    def test_e2e_40_distill_removes_queue_entries_before_pr(self):
        t = read(SKILLS, "distill", "SKILL.md")
        self.assertLess(t.index("queue-read.sh [<path>] --done"), t.index("Open it with /pr"))
        self.assertTrue(t.index("Open it with /pr") > t.rindex("--done"))
        tail = t[t.index("Open it with /pr"):]
        self.assertNotIn("queue-read.sh", tail)

    def test_e2e_40_pr_feedback_resolves_threads_before_the_last_pr_refresh(self):
        t = read(SKILLS, "pr-feedback", "SKILL.md")
        self.assertLess(t.index("resolveReviewThread"), t.index("Refresh `## Verification` once with /pr"))
        steps = re.findall(r"(?m)^(\d+)\. (.*)$", t)
        refresh = next(i for i, (_, s) in enumerate(steps) if s.startswith("Refresh `## Verification`"))
        self.assertTrue(steps[refresh + 1][1].startswith("Report:"), steps[refresh + 1])      # only the report follows
        self.assertEqual(refresh, len(steps) - 2)

    def test_e2e_40_no_other_skill_works_after_pr(self):
        for name in ("chain-intent", "chain-spec", "chain-plan"):
            t = read(SKILLS, name, "SKILL.md")
            steps = re.findall(r"(?m)^(\d+)\. (.*)$", t)
            run_pr = next(i for i, (_, s) in enumerate(steps) if s.startswith("Run /pr"))
            self.assertEqual(run_pr, len(steps) - 1, f"{name}: a numbered step follows /pr")


if __name__ == "__main__":
    unittest.main()
