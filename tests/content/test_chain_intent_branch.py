# agentive-unit: selftest.component
# design: spec.spec_stage.chain.interview, spec.spec_stage.chain.branches, docs/impl/decisions-log.md §35
"""/chain-intent creates its branch after the human agrees and resumes an earlier try of the same slug (verified on throwaway
repos with a bare origin: a branch made before the interview was left behind by an abandoned interview and made the next run fail;
a branch that only the remote has was silently re-created from the default branch and the push was rejected).

The git commands are the ones the skill text gives, in the same order, each run on its own like an agent in a headless run
(`git fetch` with an explicit refspec, `git rev-parse --verify`, `git switch`; no `git ls-remote`, no `git branch`)."""
import os
import re
import shutil
import subprocess
import tempfile
import unittest

from helpers import SKILLS, read

SLUG = "mvp"
REFSPEC = f"+refs/heads/intent/{SLUG}:refs/remotes/origin/intent/{SLUG}"


def git(cwd, *args, check=True):
    p = subprocess.run(["git", "-c", "user.name=T", "-c", "user.email=t@example.com", *args], cwd=cwd, capture_output=True, text=True)
    if check and p.returncode != 0:
        raise AssertionError(f"git {' '.join(args)}: {p.stderr}")
    return p


class SkillText(unittest.TestCase):
    def setUp(self):
        self.text = read(SKILLS, "chain-intent", "SKILL.md")
        self.steps = dict(re.findall(r"(?ms)^(\d+)\. (.*?)(?=^\d+\. |^Never push)", self.text))

    def test_branch_is_created_at_step_6_and_step_2_only_detaches(self):
        self.assertIn("git switch --detach origin/<DEFAULT>", self.steps["2"])
        self.assertNotIn("git switch -c intent/<slug> --no-track origin/<DEFAULT>", self.steps["2"])
        self.assertTrue(self.steps["6"].startswith("Unless you are resuming, create the branch now: `git switch -c intent/<slug> --no-track origin/<DEFAULT>`"))
        self.assertIn("git push -u origin HEAD", self.steps["6"])
        self.assertTrue(self.steps["7"].startswith("Run /pr"))

    def test_resume_uses_only_commands_the_headless_allowlist_has(self):
        s2 = self.steps["2"]
        for cmd in ("git fetch --quiet --no-tags origin " + REFSPEC.replace(SLUG, "<slug>"), "git rev-parse --verify --quiet intent/<slug>",
                    "git rev-parse --verify --quiet origin/intent/<slug>", "git switch intent/<slug>",
                    "git switch -c intent/<slug> --no-track origin/intent/<slug>"):
            self.assertIn(cmd, s2)
        for gone in ("ls-remote", "git branch", "git checkout"):
            self.assertNotIn(gone, s2)
        self.assertIn("/pr --explain", s2)                       # an open PR: /pr refreshes ## Verification only
        self.assertIn("go on from step 3", s2)                  # a run with no human skips the interview

    def test_the_dropped_keep_talking_sentence_is_not_added(self):
        self.assertNotIn("keep talking", self.text.lower())
        self.assertNotIn("normal to continue", self.text.lower())
        self.assertIn("There is no question limit.", self.text)  # step 4 is unchanged


class GitSteps(unittest.TestCase):
    """The commands of steps 2 and 6 on a bare origin with a default branch `main`."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="agentive-ci-")
        self.origin = os.path.join(self.tmp, "origin.git")
        self.work = os.path.join(self.tmp, "work")
        git(self.tmp, "init", "-q", "--bare", "-b", "main", self.origin)
        git(self.tmp, "clone", "-q", self.origin, self.work)
        git(self.work, "switch", "-q", "-c", "main")
        self.commit(self.work, "README.md", "one\n")
        git(self.work, "push", "-q", "-u", "origin", "main")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def commit(self, repo, name, text):
        with open(os.path.join(repo, name), "w", encoding="utf-8") as f:
            f.write(text)
        git(repo, "add", name)
        git(repo, "commit", "-q", "-m", f"add {name}")

    def probe(self, repo):
        """Step 2's look: (fetch exit code, local exists, remote exists)."""
        f = git(repo, "fetch", "--quiet", "--no-tags", "origin", REFSPEC, check=False)
        loc = git(repo, "rev-parse", "--verify", "--quiet", f"intent/{SLUG}", check=False)
        rem = git(repo, "rev-parse", "--verify", "--quiet", f"origin/intent/{SLUG}", check=False)
        return f.returncode, bool(loc.stdout.strip()), bool(rem.stdout.strip())

    def start_fresh(self, repo):
        """Neither branch exists: step 2 detaches at the fresh default branch; step 6 makes the branch."""
        git(repo, "fetch", "--quiet", "origin", "main")
        git(repo, "switch", "--detach", "origin/main")

    def make_branch(self, repo):
        git(repo, "switch", "-c", f"intent/{SLUG}", "--no-track", "origin/main")

    def test_a_new_slug_interviews_on_the_fresh_default_branch_and_leaves_no_branch_behind(self):
        other = os.path.join(self.tmp, "other")
        git(self.tmp, "clone", "-q", self.origin, other)
        self.commit(other, "feature.txt", "newer\n")
        git(other, "push", "-q", "origin", "main")
        self.assertEqual(self.probe(self.work)[1:], (False, False))
        self.assertNotEqual(self.probe(self.work)[0], 0)             # `couldn't find remote ref`: the remote has none
        self.start_fresh(self.work)                                  # the local main is old; the interview reads origin/main
        self.assertTrue(os.path.exists(os.path.join(self.work, "feature.txt")))
        self.assertEqual(git(self.work, "rev-parse", "--abbrev-ref", "HEAD").stdout.strip(), "HEAD")
        # an abandoned interview leaves nothing: the next run starts the same way
        self.assertEqual(self.probe(self.work)[1:], (False, False))
        self.start_fresh(self.work)
        # the human agrees: step 6 makes the branch from the default branch without an upstream
        self.make_branch(self.work)
        self.assertEqual(git(self.work, "config", "--get", f"branch.intent/{SLUG}.remote", check=False).stdout.strip(), "")
        self.commit(self.work, "intent.md", "intent\n")
        git(self.work, "push", "-q", "-u", "origin", "HEAD")
        self.assertEqual(self.probe(self.work), (0, True, True))

    def test_resume_when_only_the_remote_has_the_branch_continues_it_and_the_push_is_accepted(self):
        git(self.work, "switch", "-q", "-c", f"intent/{SLUG}", "--no-track", "origin/main")
        self.commit(self.work, "intent.md", "first\n")
        git(self.work, "push", "-q", "-u", "origin", "HEAD")
        remote_tip = git(self.work, "rev-parse", "HEAD").stdout.strip()
        fresh = os.path.join(self.tmp, "fresh")                      # a new clone, a cloud session, another machine
        git(self.tmp, "clone", "-q", self.origin, fresh)
        rc, local, remote = self.probe(fresh)
        self.assertEqual((rc, local, remote), (0, False, True))
        git(fresh, "switch", "-c", f"intent/{SLUG}", "--no-track", f"origin/intent/{SLUG}")
        self.assertEqual(git(fresh, "rev-parse", "HEAD").stdout.strip(), remote_tip)       # the same commit, not a fork from main
        self.assertEqual(git(fresh, "config", "--get", f"branch.intent/{SLUG}.remote", check=False).stdout.strip(), "")
        self.assertTrue(os.path.exists(os.path.join(fresh, "intent.md")))
        self.commit(fresh, "intent.md", "first\nsecond\n")
        git(fresh, "push", "-q", "-u", "origin", "HEAD")           # what /pr does: accepted, a fast-forward

    def test_resume_when_the_local_branch_exists(self):
        git(self.work, "switch", "-q", "-c", f"intent/{SLUG}", "--no-track", "origin/main")
        self.commit(self.work, "intent.md", "first\n")
        git(self.work, "switch", "-q", "main")
        self.assertEqual(self.probe(self.work)[1], True)             # even before any push: the local branch is found
        git(self.work, "switch", f"intent/{SLUG}")
        self.assertTrue(os.path.exists(os.path.join(self.work, "intent.md")))


if __name__ == "__main__":
    unittest.main()
