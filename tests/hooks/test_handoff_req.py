# agentive-unit: selftest.component
# design: spec.context_management.compact_reinject, spec.handoff, spec.stop_hook, spec.init_apply.session_start, decisions.accepted[OPENMARK], decisions.accepted[OPENWORK], decisions.accepted[HANDOFFREPORT]
"""The handoff and compact re-inject behavior of the installed hooks (decisions of 2026-11-02): the compact text keeps the
HANDOFF line and the git log when plan.md is long, carries the last failed verb and the [user] decisions of the task, the
handoff writers refuse a placeholder, an open question is the fixed [OPEN] marker, SessionStart names the open work of the
branch, and a run with no human ends with the HANDOFF line. Each class runs the hook scripts as Claude Code would (JSON on stdin)
in a repo initialized by the engine."""
import json
import os
import re
import shutil
import subprocess
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from test_hooks import ENV, GIT_ENV, Repo, StopBase, handoff, jload, rtext, schema_errors  # noqa: E402,F401


class TaskBranch(Repo):
    """A task branch with a committed docs/plans/<slug>/ and a handoff, created from main and removed again."""
    slug = "big-task"

    def git(self, *args, check=True):
        return subprocess.run(["git", "-C", self.repo] + list(args), check=check, capture_output=True, text=True, env=GIT_ENV)

    def write(self, rel, text):
        p = self.path(rel)
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, "w", encoding="utf-8") as f:
            f.write(text)

    def task_branch(self, name, files):
        """Switch to a new branch off main holding the committed files {rel: text}; the cleanup goes back to `work`."""
        def back():
            self.git("checkout", "-q", "-f", "work", check=False)
            self.git("branch", "-q", "-D", name, check=False)
            self.git("clean", "-q", "-fd", "docs", check=False)
            hd = self.path(".agents/state/handoff")
            if os.path.isdir(hd):
                for n in os.listdir(hd):
                    if n.endswith(".md"):
                        os.unlink(os.path.join(hd, n))
        self.addCleanup(back)
        self.git("checkout", "-q", "-b", name, "main")
        for rel, text in files.items():
            self.write(rel, text)
        self.git("add", "-A", "docs")
        self.git("commit", "-q", "-m", "add the task documents")

    def start(self, source, env=None):
        rc, out, err = self.hook("session-start-notice.py", {"source": source}, env=env)
        self.assertEqual(rc, 0, err)
        return out["hookSpecificOutput"]["additionalContext"], out.get("systemMessage", "")


def boxes(n):
    return "\n".join(f"- [ ] slice {i:03d}: a vertical slice that is long enough to fill the cap of the section quickly (R{i})" for i in range(n))


class CompactTextTest(TaskBranch):
    """The compact re-inject (spec.context_management.compact_reinject): the HANDOFF line is the first line and the git log is kept
    when plan.md has hundreds of open checkboxes; the checkbox list ends in `... N more`."""

    def make(self):
        self.task_branch("feature/big", {f"docs/plans/{self.slug}/plan.md": f"# Plan: big\n\n## Slices\n\n{boxes(400)}\n"})
        handoff.write(self.repo, "no_progress", failure_reason="verify failing in test: totals [2, 2] not decreasing",
                      next_step="read the log named in the last FAIL line, fix the test")

    def test_handoff_line_and_git_log_survive_a_long_plan(self):
        self.make()
        ctx, _ = self.start("compact")
        self.assertLessEqual(len(ctx), 10000)
        lines = ctx.splitlines()
        self.assertTrue(lines[0].startswith("HANDOFF=.agents/state/handoff/feature-big-"), lines[0])
        self.assertIn(f"Open checkboxes in docs/plans/{self.slug}/plan.md:", ctx)
        self.assertRegex(ctx, r"\.\.\. \d+ more")
        self.assertIn("add the task documents", ctx)               # the git log is after the checkboxes and is not cut
        self.assertLess(ctx.index("slice 000"), ctx.index("add the task documents"))

    def test_a_short_plan_is_listed_whole(self):
        self.task_branch("feature/small", {f"docs/plans/{self.slug}/plan.md": "# Plan\n\n- [ ] first (R1)\n- [x] done (R2)\n- [ ] third (R3)\n"})
        ctx, _ = self.start("compact")
        self.assertIn("- [ ] first (R1)", ctx)
        self.assertIn("- [ ] third (R3)", ctx)
        self.assertNotIn("done (R2)", ctx)
        self.assertNotIn(" more", ctx)
        self.assertNotIn("HANDOFF=", ctx)                         # none was written

    def test_only_the_handoff_of_this_branch_is_the_handoff_line(self):
        """A handoff of another branch is a HANDOFF_OTHER line; the compact text and `clear` never use it as the HANDOFF line."""
        self.task_branch("feature/other", {f"docs/plans/{self.slug}/plan.md": "# Plan\n\n- [ ] first (R1)\n"})
        handoff.write(self.repo, "no_progress", failure_reason="verify failing in test: totals [2, 2] not decreasing", next_step="read the log")
        self.git("checkout", "-q", "-b", "feature/third", "feature/other")
        for source in ("compact", "clear"):
            ctx, _ = self.start(source)
            self.assertNotIn("HANDOFF_OTHER", ctx, source)
            self.assertNotIn("HANDOFF=", ctx, source)
        self.git("checkout", "-q", "feature/other")
        self.addCleanup(self.git, "branch", "-q", "-D", "feature/third", check=False)
        self.assertTrue(self.start("clear")[0].startswith("HANDOFF=.agents/state/handoff/feature-other-"))


class CompactFailAndDecisionsTest(TaskBranch):
    """CTXREINJECT: the last failed verb with its log path (marked current or earlier tree) and the [user] decisions of the task."""

    def fail_verify(self):
        self.addCleanup(lambda: [os.path.exists(self.path(f)) and os.unlink(self.path(f)) for f in
                                 ("fail.flag", ".agents/state/verify.json")])
        self.touch("fail.flag")
        r = subprocess.run([self.path(".agents/bin/verb"), "verify"], capture_output=True, text=True, cwd=self.repo, env=ENV)
        self.assertEqual(r.returncode, 1, r.stdout + r.stderr)

    def test_failed_verb_and_log_path_with_freshness(self):
        self.task_branch("feature/red", {f"docs/plans/{self.slug}/plan.md": "# Plan\n\n- [ ] first (R1)\n"})
        self.assertNotIn("AGENTIVE_FAIL", self.start("compact")[0])     # no verify record: no section
        self.fail_verify()
        ctx, _ = self.start("compact")
        self.assertIn("Last verify: red on the current work tree", ctx)
        self.assertRegex(ctx, r"AGENTIVE_FAIL verb=test failures=1 log=\.agents/state/logs/test-\S+\.log")
        self.assertLess(ctx.index("AGENTIVE_FAIL"), ctx.index("Open checkboxes"))
        self.write("extra.txt", "a file the verify did not see\n")
        self.addCleanup(lambda: os.unlink(self.path("extra.txt")))
        ctx, _ = self.start("compact")
        self.assertIn("Last verify: red as of an earlier work tree", ctx)
        self.assertIn("AGENTIVE_FAIL verb=test", ctx)

    def test_green_verify_has_no_failed_verb_section(self):
        self.task_branch("feature/green", {f"docs/plans/{self.slug}/plan.md": "# Plan\n\n- [ ] first (R1)\n"})
        subprocess.run([self.path(".agents/bin/verb"), "verify"], capture_output=True, text=True, cwd=self.repo, env=ENV)
        self.addCleanup(lambda: os.path.exists(self.path(".agents/state/verify.json")) and os.unlink(self.path(".agents/state/verify.json")))
        self.assertNotIn("Last verify", self.start("compact")[0])

    def test_user_decisions_of_the_task_files(self):
        spec = ("Intent: docs/plans/big-task/intent.md\n\n## Decisions\n\n- [user] keep the queue in Postgres, no new broker\n"
                "- [inferred] retry three times\n- [user] | [doc: <path or URL>] | [inferred] <decision>\n")
        plan = "# Plan\n\n- [ ] first (R1)\n\n## Decisions\n\n- [user] ship behind the flag first (R1)\n- [doc: README.md] note\n"
        self.task_branch("feature/dec", {f"docs/plans/{self.slug}/spec.md": spec, f"docs/plans/{self.slug}/plan.md": plan,
                                         "docs/plans/other-task/plan.md": "# Plan\n\n- [user] a decision of a second task folder\n"})
        ctx, _ = self.start("compact")
        self.assertIn(f"[user] decisions in docs/plans/{self.slug}/spec.md:", ctx)
        self.assertIn("- [user] keep the queue in Postgres, no new broker", ctx)
        self.assertIn(f"[user] decisions in docs/plans/{self.slug}/plan.md:", ctx)
        self.assertIn("- [user] ship behind the flag first (R1)", ctx)
        self.assertNotIn("retry three times", ctx)                       # [inferred] is not a [user] decision
        self.assertNotIn("<decision>", ctx)                              # the template line is not one either
        self.assertIn("[user] decisions in docs/plans/other-task/plan.md:", ctx)   # every docs/plans/<slug>/ the branch changed
        self.assertLess(ctx.index("Open checkboxes"), ctx.index("[user] decisions in"))
        self.assertLess(ctx.index("[user] decisions in"), ctx.index("Commits on this branch"))

    def test_total_stays_within_the_cap_with_every_section_full(self):
        body = "# Plan\n\n" + boxes(400) + "\n\n## Decisions\n\n" + "\n".join(f"- [user] decision number {i} " + "x" * 60 for i in range(100)) + "\n"
        self.task_branch("feature/full", {f"docs/plans/{self.slug}/plan.md": body})
        for i in range(60):
            self.write(f"f{i}.txt", "x\n")
            self.git("add", f"f{i}.txt")
            self.git("commit", "-q", "-m", f"commit number {i:02d} with a message that takes some room in the log")
        self.fail_verify()
        handoff.write(self.repo, "no_progress", failure_reason="verify failing in test: totals [2, 2] not decreasing", next_step="read the log")
        ctx, _ = self.start("compact")
        self.assertLessEqual(len(ctx), 10000)
        for needle in ("HANDOFF=", "AGENTIVE_FAIL verb=test", "Open checkboxes", "[user] decisions in", "Commits on this branch"):
            self.assertIn(needle, ctx)
        self.assertEqual(ctx.count("... "), 3, ctx)                       # checkboxes, decisions and the log each end in `... N more`


class PlaceholderUnitTest(unittest.TestCase):
    def test_is_placeholder(self):
        for text in ("", "   ", "TBD", "todo", "n/a", "...", "(agent: fill in)", "(agent: fill in) verify failing in test", "<the question>",
                     "- - -", "later."):
            self.assertTrue(handoff.is_placeholder(text), text)
        for text in ("rerun lint", "the next concrete step", "Drop the users table?", "none of the three options worked"):
            self.assertFalse(handoff.is_placeholder(text), text)           # a short real value is a placeholder only with a length rule
        self.assertTrue(handoff.is_placeholder("rerun lint", handoff.PLACEHOLDER_MIN))
        self.assertFalse(handoff.is_placeholder("Drop the users table in production?", handoff.PLACEHOLDER_MIN))


class HandoffPlaceholderTest(StopBase):
    """HANDOFFFILL: a writer with arguments refuses a placeholder; the script fills what the agent left on NO-PROGRESS."""
    SH = ".agents/skills/chain-spec/scripts/handoff.sh"

    def handoff_files(self):
        hd = self.path(".agents/state/handoff")
        return sorted(n for n in os.listdir(hd) if n.endswith(".md")) if os.path.isdir(hd) else []

    def sh(self, *args):
        r = subprocess.run(["sh", self.path(self.SH)] + list(args), capture_output=True, text=True, cwd=self.repo, env=ENV)
        return r.returncode, r.stdout.strip()

    def test_handoff_sh_refuses_empty_and_placeholder_values(self):
        for q, n in (("", "ask the data owner which tables may be dropped"), ("TBD", "ask the data owner which tables may be dropped"),
                     ("May the migration drop the legacy users table?", "(agent: fill in)"), ("Drop users?", "ask"), ("<the question>", "<what>")):
            rc, out = self.sh(q, n)
            self.assertEqual(rc, 2, (q, n, out))
            self.assertRegex(out, r"^AGENTIVE_HANDOFF=stop reason=placeholder fields=\S+; NEXT=pass a concrete statement of at least 20 characters")
            self.assertEqual(self.handoff_files(), [])                       # nothing was written
        self.assertIn("fields=failure_reason,next_step", self.sh("<the question>", "<what>")[1])
        self.assertIn("fields=failure_reason;", self.sh("TBD", "ask the data owner which tables may be dropped")[1])

    def test_handoff_sh_writes_a_concrete_value(self):
        rc, out = self.sh("May the migration drop the legacy users table?", "the data owner decides which tables may be dropped")
        self.assertEqual(rc, 0, out)
        self.assertRegex(out, r"^HANDOFF=\.agents/state/handoff/work-[0-9a-f]{8}\.md$")
        doc = handoff.read(self.path(out[len("HANDOFF="):]))
        self.assertEqual((doc["reason"], doc["untried"]), ("open_question", ""))        # an empty untried stays valid
        self.assertEqual(schema_errors("handoff", doc), [])

    def test_write_command_checks_untried_only_when_given(self):
        env = dict(ENV, PYTHONPATH=self.path(".agents/lib"))
        def write(*extra):
            r = subprocess.run([sys.executable, "-B", "-m", "agentive.handoff", "write", "--reason", "open_question", "--failure-reason",
                                "May the migration drop the legacy users table?", "--next-step", "the data owner decides which tables go"] + list(extra),
                               capture_output=True, text=True, cwd=self.repo, env=env)
            return r.returncode, r.stdout.strip()
        rc, out = write("--untried", "(agent: fill in)")
        self.assertEqual((rc, out.split(";")[0]), (2, "AGENTIVE_HANDOFF=stop reason=placeholder fields=untried"))
        self.assertEqual(self.handoff_files(), [])
        self.assertEqual(write("--untried", "none")[0], 0)                # a real word is a valid untried
        self.assertEqual(write()[0], 0)

    def no_progress(self):
        self.touch("fail.flag")
        self.assertEqual(self.stop()[0], 2)
        rc, out, err = self.stop()
        self.assertEqual(rc, 0)
        self.assertIn("marked '(script)'", out["hookSpecificOutput"]["additionalContext"])
        path = self.path(".agents/state/handoff/" + self.handoff_files()[0])
        self.assertIn("(agent: fill in)", handoff.read(path)["failure_reason"])
        return path

    def test_no_progress_placeholders_are_filled_with_facts_by_the_next_stop(self):
        path = self.no_progress()
        rc, out, err = self.stop()                                          # the Stop after the one continuation
        self.assertEqual((rc, out), (0, ""))
        doc = handoff.read(path)
        self.assertEqual(schema_errors("handoff", doc), [])
        self.assertTrue(doc["failure_reason"].startswith("(script) verify failing in test; last line: AGENTIVE_FAIL verb=test exit=1 log="), doc["failure_reason"])
        self.assertTrue(doc["next_step"].startswith("(script) the agent left no next step; the last failing line says NEXT=grep -n"), doc["next_step"])
        self.assertEqual(doc["untried"], "(script) not recorded by the agent")
        self.assertFalse(jload(self.path(".agents/state/stop.json"))["handoff_requested"])
        rc, out, err = self.hook("session-start-notice.py", {"source": "resume"})
        self.assertRegex(out["hookSpecificOutput"]["additionalContext"], r"\nHANDOFF=\S+ agent_notes=missing; NEXT=ask_user:resume")

    def test_values_the_agent_wrote_are_kept(self):
        path = self.no_progress()
        handoff.write(self.repo, "no_progress", failure_reason="lint passes, test fails on the empty queue", next_step="rerun test",
                      untried="(agent: fill in)")                                 # a short real next_step; untried left as a placeholder
        self.assertEqual(self.stop()[0], 0)
        doc = handoff.read(path)
        self.assertEqual((doc["failure_reason"], doc["next_step"]), ("lint passes, test fails on the empty queue", "rerun test"))
        self.assertEqual(doc["untried"], "(script) not recorded by the agent")
        rc, out, err = self.hook("session-start-notice.py", {"source": "resume"})
        self.assertIn("agent_notes=missing", out["hookSpecificOutput"]["additionalContext"])

    def test_nothing_to_fill_when_the_agent_filled_all_three(self):
        path = self.no_progress()
        handoff.write(self.repo, "no_progress", failure_reason="lint passes, test fails on the empty queue", next_step="make the queue return an empty list",
                      untried="a retry with a longer timeout")
        self.assertEqual(self.stop()[0], 0)
        doc = handoff.read(path)
        self.assertFalse(any(str(doc[k]).startswith("(script)") for k in ("failure_reason", "untried", "next_step")))
        rc, out, err = self.hook("session-start-notice.py", {"source": "resume"})
        self.assertNotIn("agent_notes", out["hookSpecificOutput"]["additionalContext"])

    def test_blocked_command_is_part_of_the_facts(self):
        handoff.write(self.repo, "ask_class_denied", failure_reason="(agent: fill in)", next_step="(agent: fill in)",
                      blocked_command="git push --force origin main (rule force-push)")
        filled = handoff.fill_placeholders(self.repo, {"failure_reason": "guard rule needs a human", "next_step": "ask the human", "untried": ""})
        self.assertEqual(filled, ["failure_reason", "next_step"])
        doc = handoff.read(handoff.current_path(self.repo))
        self.assertEqual(doc["failure_reason"], "(script) guard rule needs a human; blocked command: git push --force origin main (rule force-push)")
        self.assertEqual(schema_errors("handoff", doc), [])


HEADLESS = dict(ENV, AGENTIVE_HEADLESS="1")
SPEC_OPEN = ("Intent: docs/plans/openq/intent.md\n\n## Open questions from the intent\n\n- Which index? -> answered: btree\n"
             "- [OPEN] May the migration drop the legacy users table?\n- [OPEN] Is the retention period 30 or 90 days?\n")


class OpenQuestionStopTest(TaskBranch):
    """OPENMARK: with no human present the Stop hook asks once for the open_question handoff of a spec/<slug> branch whose spec.md
    has an [OPEN] bullet; it never blocks a run with a human, a branch of another kind, an answered spec or a branch with a handoff."""

    def setUp(self):
        for f in ("stop.json", "verify.json", "dirty.json"):
            if os.path.exists(self.path(".agents/state/" + f)):
                os.unlink(self.path(".agents/state/" + f))
        shutil.rmtree(self.path(".agents/state/handoff"), ignore_errors=True)

    def stop(self, env=HEADLESS, tool="claude"):
        return self.hook("stop-verify.py", {"stop_hook_active": False}, tool=tool, env=env)

    def spec_branch(self, name="spec/openq", spec=SPEC_OPEN):
        self.task_branch(name, {"docs/plans/openq/spec.md": spec})

    def asked(self, out):
        return isinstance(out, dict) and "OPEN_QUESTION:" in json.dumps(out)

    def test_asks_once_for_the_handoff(self):
        self.spec_branch()
        rc, out, err = self.stop()
        self.assertEqual(rc, 0, err)
        text = out["hookSpecificOutput"]["additionalContext"]
        self.assertTrue(text.startswith("OPEN_QUESTION: docs/plans/openq/spec.md has 2 [OPEN] question(s) and this branch has no handoff."), text)
        self.assertIn('sh .agents/skills/chain-spec/scripts/handoff.sh "<the first [OPEN] question in your words>"', text)
        self.assertIn("First question: May the migration drop the legacy users table?; NEXT=run the handoff.sh command", text)
        st = jload(self.path(".agents/state/stop.json"))
        self.assertTrue(st["open_question_nudged"])
        self.assertEqual((st["consecutive_blocks"], st["total_blocks"]), (0, 0))      # the counters are not touched
        self.assertEqual(schema_errors("stop-state", st), [])
        self.assertFalse(os.path.exists(self.path(".agents/state/verify.json")))      # no verify rerun for the nudge
        rc, out, err = self.stop()                                                    # the second Stop: back to the normal path
        self.assertEqual(rc, 0, err)
        self.assertFalse(self.asked(out), out)

    def test_codex_gets_a_decision_block(self):
        self.spec_branch()
        rc, out, err = self.stop(tool="codex")
        self.assertEqual((rc, out["decision"]), (0, "block"))
        self.assertIn("OPEN_QUESTION:", out["reason"])

    def test_not_asked_when_a_human_is_present(self):
        self.spec_branch()
        self.assertFalse(self.asked(self.stop(env=ENV)[1]))
        self.assertFalse(jload(self.path(".agents/state/stop.json")).get("open_question_nudged"))

    def test_not_asked_when_the_branch_already_has_a_handoff(self):
        self.spec_branch()
        handoff.write(self.repo, "open_question", failure_reason="May the migration drop the legacy users table?",
                      next_step="the data owner decides which tables may be dropped")
        self.assertFalse(self.asked(self.stop()[1]))

    def test_not_asked_for_other_branches_or_an_answered_spec(self):
        self.spec_branch("feature/openq")
        self.assertFalse(self.asked(self.stop()[1]))
        self.spec_branch("spec/answered", SPEC_OPEN.replace("- [OPEN]", "- answered:"))
        self.assertFalse(self.asked(self.stop()[1]))
        self.spec_branch("spec/nospec", SPEC_OPEN)
        os.unlink(self.path("docs/plans/openq/spec.md"))
        self.assertFalse(self.asked(self.stop()[1]))

    def test_a_new_start_asks_again(self):
        self.spec_branch()
        self.assertTrue(self.asked(self.stop()[1]))
        self.assertFalse(self.asked(self.stop()[1]))
        rc, out, err = self.hook("session-start-notice.py", {"source": "startup", "session_id": "s2"})
        self.assertFalse(jload(self.path(".agents/state/stop.json"))["open_question_nudged"])
        rc, out, err = self.hook("stop-verify.py", {"stop_hook_active": False, "session_id": "s2"}, env=HEADLESS)
        self.assertTrue(self.asked(out), out)


class OpenWorkTest(TaskBranch):
    """OPENWORK: startup and resume name the open plan.md checkboxes of the branch's task, with or without a handoff file."""
    PLAN = "# Plan\n\n## Slices\n\n- [x] done slice (R1)\n- [ ] parser accepts the v2 header (R2)\n- [ ] writer emits the v2 header (R2, R3)\n"

    def test_open_checkboxes_without_a_handoff_file(self):
        self.task_branch("feature/work", {f"docs/plans/{self.slug}/plan.md": self.PLAN})
        for source in ("startup", "resume"):
            ctx, _ = self.start(source)
            lines = ctx.splitlines()
            self.assertIn(f"OPEN_WORK=docs/plans/{self.slug}/plan.md; NEXT=continue with the first open checkbox", lines, source)
            self.assertIn("- [ ] parser accepts the v2 header (R2)", lines)
            self.assertNotIn("done slice", ctx)
            self.assertNotIn("HANDOFF=", ctx)

    def test_with_a_handoff_file_both_lines_are_there(self):
        self.task_branch("feature/work2", {f"docs/plans/{self.slug}/plan.md": self.PLAN})
        handoff.write(self.repo, "no_progress", failure_reason="verify failing in test: totals [2, 2] not decreasing", next_step="read the log")
        ctx, _ = self.start("startup")
        self.assertRegex(ctx, r"(?m)^HANDOFF=\.agents/state/handoff/feature-work2-\S+; NEXT=ask_user:resume")
        self.assertIn("OPEN_WORK=", ctx)

    def test_no_human_gets_it_too(self):
        self.task_branch("feature/work3", {f"docs/plans/{self.slug}/plan.md": self.PLAN})
        ctx, _ = self.start("startup", env=dict(ENV, AGENTIVE_HEADLESS="1"))
        self.assertIn("OPEN_WORK=docs/plans/", ctx)

    def test_nothing_on_the_default_branch_or_without_open_boxes_or_on_other_sources(self):
        self.assertNotIn("OPEN_WORK", self.start("startup")[0])                       # branch `work`, no plan.md
        self.task_branch("feature/done", {f"docs/plans/{self.slug}/plan.md": "# Plan\n\n- [x] all done (R1)\n"})
        self.assertNotIn("OPEN_WORK", self.start("startup")[0])
        self.task_branch("feature/open", {f"docs/plans/{self.slug}/plan.md": self.PLAN})
        self.assertNotIn("OPEN_WORK", self.start("clear")[0])
        self.git("checkout", "-q", "main")
        self.assertNotIn("OPEN_WORK", self.start("startup")[0])
        self.assertNotIn("OPEN_WORK", self.start("compact")[0])                       # compact has its own section format

    def test_capped_at_1500_characters_with_a_more_line(self):
        self.task_branch("feature/long", {f"docs/plans/{self.slug}/plan.md": "# Plan\n\n" + boxes(200) + "\n"})
        ctx, _ = self.start("startup")
        rest = ctx[ctx.index("OPEN_WORK="):]
        work = rest[:re.search(r"\.\.\. \d+ more", rest).end()]               # up to and including the `... N more` line
        self.assertLessEqual(len(work), 1500)
        self.assertRegex(work, r"\.\.\. \d+ more")
        self.assertIn("slice 000", work)


class HandoffReportTest(StopBase):
    """HANDOFFREPORT: a run with no human present ends with the HANDOFF line; a run with a human is unchanged."""
    LINE = r"HANDOFF=\.agents/state/handoff/work-[0-9a-f]{8}\.md reason=%s blocked=%s next=%s"

    def setUp(self):
        super().setUp()
        rc, out, err = self.hook("session-start-notice.py", {"source": "startup"})       # the new start sets started_at, before any handoff
        self.assertEqual(rc, 0, err)

    def stop_as(self, env, tool="claude"):
        return self.hook("stop-verify.py", {"stop_hook_active": False}, tool=tool, env=env)

    def test_no_progress_run_without_a_human_ends_with_the_line(self):
        self.touch("fail.flag")
        self.assertEqual(self.stop_as(HEADLESS)[0], 2)
        rc, out, err = self.stop_as(HEADLESS)                                   # NO-PROGRESS: the one continuation
        self.assertEqual(rc, 0)
        self.assertNotIn("systemMessage", out)
        rc, out, err = self.stop_as(HEADLESS)                                   # the Stop after it: the handoff is filled, the line is the message
        self.assertEqual(rc, 0, err)
        self.assertRegex(out["systemMessage"], "^" + self.LINE % ("no_progress", "none", r"\(script\) the agent left no next step; the last failing line says NEXT=grep -n.*") + "$")
        self.assertEqual(len(out["systemMessage"].splitlines()), 1)
        self.touch("fail.flag", False)
        rc, out, err = self.stop_as(HEADLESS)                                   # fixed: DONE, still ends with the line
        lines = out["systemMessage"].splitlines()
        self.assertRegex(lines[0], "^" + self.LINE % ("no_progress", "none", ".*") + "$")
        self.assertTrue(lines[1].startswith("DONE"), lines)

    def test_a_run_with_a_human_is_unchanged(self):
        self.touch("fail.flag")
        self.stop_as(ENV)
        self.stop_as(ENV)
        self.assertEqual(self.stop_as(ENV)[1:], ("", ""))
        self.touch("fail.flag", False)
        rc, out, err = self.stop_as(ENV)
        self.assertNotIn("HANDOFF=", out["systemMessage"])

    def test_a_handoff_of_an_earlier_session_is_not_reported(self):
        handoff.write(self.repo, "no_progress", failure_reason="verify failing in test: totals [2, 2] not decreasing", next_step="read the log named in the last line")
        self.stop_as(HEADLESS)                                                  # creates the stop state of this session (started_at = now)
        st = jload(self.path(".agents/state/stop.json"))
        st["started_at"] = "2999-01-01T00:00:00Z"
        with open(self.path(".agents/state/stop.json"), "w") as f:
            json.dump(st, f)
        rc, out, err = self.stop_as(HEADLESS)
        self.assertNotIn("HANDOFF=", out["systemMessage"])
        st["started_at"] = "2000-01-01T00:00:00Z"
        with open(self.path(".agents/state/stop.json"), "w") as f:
            json.dump(st, f)
        rc, out, err = self.hook("stop-verify.py", {"stop_hook_active": False}, env=HEADLESS)
        self.assertRegex(out["systemMessage"].splitlines()[0], "^" + self.LINE % ("no_progress", "none", "read the log named in the last line") + "$")

    def test_started_at_is_set_by_a_new_start(self):
        rc, out, err = self.hook("session-start-notice.py", {"source": "startup", "session_id": "sx"})
        st = jload(self.path(".agents/state/stop.json"))
        self.assertRegex(st["started_at"], r"^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ$")
        self.assertEqual(schema_errors("stop-state", st), [])

    def test_open_question_handoff_is_reported_too(self):
        handoff.write(self.repo, "open_question", failure_reason="May the migration drop the legacy users table?",
                      next_step="the data owner decides which tables may be dropped")
        rc, out, err = self.hook("stop-verify.py", {"stop_hook_active": False}, env=HEADLESS)
        self.assertRegex(out["systemMessage"].splitlines()[0], "^" + self.LINE % ("open_question", "none", "the data owner decides which tables may be dropped") + "$")

    def test_guard_deny_reason_asks_for_the_line_as_the_last_message(self):
        rc, out, err = self.hook("pre-tool-use-guard.py", {"tool_name": "Bash", "tool_input": {"command": "git reset --hard HEAD~1"}}, env=HEADLESS)
        reason = out["hookSpecificOutput"]["permissionDecisionReason"]
        m = re.search(r"\(no human present; handed off in (\S+?); end your final message with this line: (HANDOFF=.*?)\)\. NEXT: ", reason)
        self.assertTrue(m, reason)
        self.assertRegex(m.group(2), "^" + self.LINE % ("ask_class_denied", r"git reset --hard HEAD~1 \(rule reset-hard\)", ".+") + "$")
        self.assertTrue(m.group(2).startswith("HANDOFF=" + m.group(1)))
        # the Stop hook of the same session prints the same line
        rc, out, err = self.hook("stop-verify.py", {"stop_hook_active": False}, env=HEADLESS)
        self.assertEqual(out["systemMessage"].splitlines()[0], m.group(2))


if __name__ == "__main__":
    unittest.main()
