# agentive-unit: selftest.component
# design: spec.work_loop.pr_skill, contracts[precommit-review], decisions.accepted[HEADLESSHINT], spec.verb_wrapper, docs/impl/decisions-log.md §29
"""End-to-end run 2026-11-01 (xrpz/agentive-e2e), the repo-layer half:

E2E-44  a previous change's .agents/state/precommit-review.json was copied into a new PR body (`source: precommit-review verdict:
        pass ... reviewed_ref: 2d87e6b`) although that change had never been reviewed: the record binds to the reviewed tree, and
        a record of another tree is dropped with NOTE PRECOMMIT_STALE and a NEXT (the U2 confirmation in it counts too).
E2E-46  (a) the AGENTS.md template says how to create a branch under the headless allowlist (`git switch -c <name> --no-track`,
        not `git checkout -b`); (b) the installed entry points print `NEXT=install python3 >= 3.13` on an older interpreter,
        the same text as the engine."""
import json
import os
import re
import stat
import subprocess
import sys
import tempfile
import unittest

from helpers import LIB, ROOT, SRC, Sandbox, git, read, write
import test_prbody as tp
from test_prbody import CODE_REVIEW, block

sys.path.insert(0, LIB)

BIN = os.path.join(SRC, "dot-agents", "bin")
SKILLS = os.path.join(SRC, "dot-agents", "skills")
FLOOR_NEXT = "NEXT=install python3 >= 3.13"


def record(tree, **kw):
    rec = {"blocking": [], "nits": [], "at": "2026-10-28T00:00:00Z", "head_sha": "0a07c31", "tree": tree,
           "confirm_scope_hits": [{"path": ".github/workflows/ci.yml", "category": "ci", "glob": ".github/workflows/**"}],
           "u2": {"run": "interactive", "human_confirmation": "obtained", "action": "commit"},
           "reviewer": {"reviewer_id": "correctness-reviewer", "tool": "claude_code", "executed_by": "subagent",
                        "attestation": "self_reported", "context": "unverified", "session_id": "rev-1", "reviewed_ref": tree}}
    rec.update(kw)
    return rec


class E2E44StaleRecord(unittest.TestCase):
    commit, verify_json, draft = tp.Flow.commit, tp.Flow.verify_json, tp.Flow.draft

    def setUp(self):
        self.sb = Sandbox({"README.md": "# demo\n", "code_review.md": CODE_REVIEW, "src/app.py": "x = 1\n"})
        self.env = self.sb.fake_gh([])
        git(self.sb.repo, "switch", "-q", "-c", "ci/change")
        self.commit({".github/workflows/ci.yml": "on: push\n"})
        self.verify_json()
        self.tree = git(self.sb.repo, "rev-parse", "HEAD^{tree}")
        self.old_tree = "2d87e6b" + "0" * 33          # the tree of the previous change (reviewed_ref 2d87e6b in PR #1)

    def tearDown(self):
        self.sb.close()

    def reviews(self):
        return (block(read(self.sb.repo, ".agents", "state", "pr-body.md"))["agentive-review"]).get("reviews")

    def test_e2e_44_record_of_another_tree_is_dropped_with_a_note_and_a_next(self):
        from helpers import schema_errors
        rec = record(self.old_tree)
        self.assertEqual(schema_errors("precommit-review", rec), [])
        write(self.sb.repo, ".agents/state/precommit-review.json", json.dumps(rec))
        rc, out = self.draft()
        self.assertEqual(rc, 0, out)
        lines = out.strip().splitlines()
        note = next(ln for ln in lines if ln.startswith("NOTE PRECOMMIT_STALE"))
        self.assertIn("reviewed tree 2d87e6b00000", note)
        self.assertIn(f"content is tree {self.tree[:12]}", note)
        self.assertIn("NEXT=run /precommit-review on this content", note)
        self.assertIn("git reset --soft origin/main", note)
        self.assertEqual(sum(1 for ln in lines if "PRECOMMIT_STALE" in ln), 1, out)          # once, although several parts read the record
        self.assertTrue(lines[-1].startswith("PR_BODY="), lines[-1])                          # the last line is still the agent's NEXT
        self.assertEqual(self.reviews(), [])
        body = read(self.sb.repo, ".agents", "state", "pr-body.md")
        self.assertNotIn("precommit-review", body)
        self.assertNotIn("2d87e6b", body)
        # the U2 confirmation of the stale record does not carry over
        self.assertIn("U2=not_obtained", out)
        self.assertEqual(block(body)["agentive-review"]["u2"]["human_confirmation"], "not_obtained")

    def test_e2e_44_record_of_this_tree_is_copied_without_a_note(self):
        write(self.sb.repo, ".agents/state/precommit-review.json", json.dumps(record(self.tree)))
        rc, out = self.draft()
        self.assertNotIn("PRECOMMIT_STALE", out)
        self.assertIn("U2=obtained", out)
        rv = self.reviews()[0]
        self.assertEqual((rv["source"], rv["verdict"], rv["reviewed_ref"]), ("precommit-review", "pass", self.tree))

    def test_e2e_44_a_later_commit_makes_the_record_stale(self):
        write(self.sb.repo, ".agents/state/precommit-review.json", json.dumps(record(self.tree)))
        self.commit({"src/app.py": "x = 2\n"}, "unreviewed follow-up")
        self.verify_json()
        rc, out = self.draft()
        self.assertIn("NOTE PRECOMMIT_STALE", out)
        self.assertEqual(self.reviews(), [])

    def test_e2e_44_older_records_bind_by_reviewer_reviewed_ref_or_not_at_all(self):
        old = record(self.tree)
        del old["tree"]
        write(self.sb.repo, ".agents/state/precommit-review.json", json.dumps(old))        # no tree, reviewed_ref = HEAD^{tree}: still bound
        rc, out = self.draft()
        self.assertNotIn("PRECOMMIT_STALE", out)
        self.assertEqual(len(self.reviews()), 1)
        nameless = record(self.tree)
        del nameless["tree"]
        del nameless["reviewer"]["reviewed_ref"]                                             # names no tree at all: cannot be bound
        write(self.sb.repo, ".agents/state/precommit-review.json", json.dumps(nameless))
        rc, out = self.draft()
        self.assertIn("NOTE PRECOMMIT_STALE .agents/state/precommit-review.json reviewed no tree", out)
        self.assertEqual(self.reviews(), [])

    def test_e2e_44_no_record_no_note(self):
        rc, out = self.draft()
        self.assertNotIn("PRECOMMIT_STALE", out)
        self.assertEqual(self.reviews(), [])

    def test_e2e_44_pr_skill_documents_the_note(self):
        self.assertIn("PRECOMMIT_STALE", read(SKILLS, "pr", "SKILL.md"))


def fake_python(directory, minor=12):
    """A `python3` that reports Python 3.<minor> (sys.version_info) and otherwise runs like the real interpreter."""
    path = os.path.join(directory, "python3")
    with open(path, "w", encoding="utf-8") as f:
        f.write(f"""#!{sys.executable}
import runpy, sys
sys.version_info = (3, {minor}, 0, "final", 0)
args = sys.argv[1:]
while args and args[0] in ("-B", "-I"):
    args = args[1:]
if args and args[0] == "-c":
    sys.argv = ["-c"] + args[2:]
    exec(compile(args[1], "<string>", "exec"), {{"__name__": "__main__"}})
elif args and args[0] == "-m":
    sys.argv = args[1:]
    runpy.run_module(args[1], run_name="__main__", alter_sys=True)
else:
    sys.argv = args
    runpy.run_path(args[0], run_name="__main__")
""")
    os.chmod(path, os.stat(path).st_mode | stat.S_IXUSR)
    return path


class E2E46FloorCheck(unittest.TestCase):
    def setUp(self):
        self.sb = Sandbox({"README.md": "# demo\n", "code_review.md": CODE_REVIEW})
        self.addCleanup(self.sb.close)
        self.shims = tempfile.mkdtemp(prefix="agentive-oldpy-")
        self.addCleanup(lambda: subprocess.run(["rm", "-rf", self.shims]))

    def run_with(self, minor, argv, stdin=""):
        d = os.path.join(self.shims, f"py3{minor}")
        os.makedirs(d, exist_ok=True)
        fake_python(d, minor)
        env = dict(os.environ, PATH=d + os.pathsep + os.environ["PATH"])
        env.pop("PYTHONPATH", None)
        r = subprocess.run(argv, cwd=self.sb.repo, env=env, input=stdin, capture_output=True, text=True)
        return r.returncode, r.stdout + r.stderr

    def entry_points(self):
        return [([os.path.join(BIN, "verb"), "lint"], "AGENTIVE_FAIL verb=lint exit=127 log=none; " + FLOOR_NEXT),
                ([os.path.join(BIN, "agentive-lint"), "ci"], "AGENTIVE_ERROR python3 < 3.13; " + FLOOR_NEXT),
                ([os.path.join(BIN, "agentive-review"), "outer-verbs", "--out", "x.json"], "AGENTIVE_ERROR python3 < 3.13; " + FLOOR_NEXT),
                ([os.path.join(BIN, "agentive-reviewer-eval"), "detect", "--base", "a", "--head", "b"],
                 "AGENTIVE_ERROR python3 < 3.13; " + FLOOR_NEXT),
                (["sh", os.path.join(SKILLS, "pr", "scripts", "pr-body.sh"), "draft"], "AGENTIVE_PR=stop reason=python3_too_old; " + FLOOR_NEXT),
                (["sh", os.path.join(SKILLS, "distill", "scripts", "eval-case.sh"), "x"], "EVALCASE=stop reason=python3_too_old; " + FLOOR_NEXT),
                (["sh", os.path.join(SKILLS, "distill", "scripts", "queue-read.sh")], "QUEUE=stop reason=python3_too_old; " + FLOOR_NEXT),
                (["sh", os.path.join(SKILLS, "chain-spec", "scripts", "handoff.sh"), "show"],
                 "AGENTIVE_HANDOFF=stop reason=python3_too_old; " + FLOOR_NEXT),
                (["sh", os.path.join(SKILLS, "pr-feedback", "scripts", "pr-state.sh"), "1"], "PR_STATE=stop reason=python3_too_old; " + FLOOR_NEXT)]

    def test_e2e_46_entry_points_print_the_engine_floor_text_on_python_312(self):
        engine = read(ROOT, "src", "engine", "apply.py")
        self.assertIn('print("AGENTIVE_ERROR python3 < 3.13; NEXT=install python3 >= 3.13")', engine)       # the text the layer repeats
        for argv, expect in self.entry_points():
            rc, out = self.run_with(12, argv)
            self.assertNotEqual(rc, 0, (argv, out))
            self.assertEqual(out.strip().splitlines()[-1], expect, (argv, out))

    def test_e2e_46_python_313_runs_normally(self):
        for argv, expect in self.entry_points():
            rc, out = self.run_with(13, argv)
            self.assertNotIn(FLOOR_NEXT, out, (argv, out))
        rc, out = self.run_with(13, [os.path.join(BIN, "verb"), "lint"])
        self.assertNotIn("python3", out.split(";")[-1] if "AGENTIVE_FAIL" in out else "", out)

    def test_e2e_46_every_shell_wrapper_that_needs_python_has_the_check(self):
        """No installed shell script that execs python3 can skip the floor check (a new wrapper must add it)."""
        missing = []
        for base, _, names in os.walk(os.path.join(SRC, "dot-agents")):
            for n in names:
                p = os.path.join(base, n)
                if "__pycache__" in p or not (n.endswith(".sh") or os.path.basename(base) == "bin"):
                    continue
                text = read(p)
                if not text.startswith("#!/bin/sh") or "python3" not in text:
                    continue
                if "(3, 13)" not in text:
                    missing.append(os.path.relpath(p, SRC))
        self.assertEqual(missing, [])
        for n in ("agentive-lint", "agentive-review", "agentive-reviewer-eval"):
            self.assertIn("if sys.version_info < (3, 13):", read(BIN, n), n)
        self.assertIn("if sys.version_info < (3, 13):", read(SRC, "dot-agents", "hooks", "session-start-notice.py"))

    def test_e2e_46_agents_md_template_says_how_to_branch_under_the_headless_allowlist(self):
        text = read(SRC, "AGENTS.md.regions", "context.md")
        line = next(ln for ln in text.splitlines() if ln.startswith("Branches:"))
        self.assertIn("git switch -c <type>/<slug> --no-track origin/<default branch>", line)
        self.assertIn("git checkout -b", line)
        self.assertIn("denies", line)
        # the same command the chain skills give
        for skill in ("chain-intent", "chain-spec", "chain-plan"):
            self.assertRegex(read(SKILLS, skill, "SKILL.md"), r"git switch -c \w+/<slug> --no-track origin/<DEFAULT>")
        # and the allowlist really has it: the standard headless command allows `git switch *` and no `git checkout`
        design = read(ROOT, "design", "framework.yaml")
        std = next(ln for ln in design.splitlines() if ln.strip().startswith("standard_cmd:"))
        self.assertIn("Bash(git switch *)", std)
        self.assertNotIn("Bash(git checkout", std)
        # the generated root-level text of an installed repo carries the line (engine render of the region)
        installed = read(ROOT, "plugins", "claude-code", "agentive", "payload", "repo-layer", "AGENTS.md.regions", "context.md")
        self.assertIn(line, installed)


if __name__ == "__main__":
    unittest.main()
