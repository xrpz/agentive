# agentive-unit: selftest.upgrade_matrix
# design: spec.init_apply (update, managed_regions, drift), spec.framework_repo.self_tests, docs/IMPLEMENTATION.md §6 (upgrade_matrix)
"""Upgrade from every earlier release (impl26 B2). For each release in releases.json: install that release's repo layer into a copy
of fixtures/py with THAT release's engine (its payload comes out of git: `git archive <ref> plugins/claude-code/agentive/payload`),
make a user edit in an unmanaged place (README.md) and in `.agents/custom/` (a rubric override), then run the CURRENT engine's
`update` and assert: exit 0, no drift/conflict/error report on the untouched managed regions, both user edits kept, a second update
changes nothing, and the result equals a fresh init + the same edits + update, except for timestamps and version fields.
A second case deletes managed files after the old install and checks that update restores them (a partial install is recovered).
Precedents: Fission-AI/OpenSpec runs its upgrade CI from five old versions; EveryInc/compound-engineering 487c5e0 recovers a partial
install. One fixture stack (py) keeps the run small."""
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, os.path.join(ROOT, "tests", "sample_repo"))
import test_init_apply as T  # noqa: E402  (helpers: git, copy_fixture, snapshot, engine, changed)

PAYLOAD_PATH = "plugins/claude-code/agentive/payload"
USER_README = "\nUser note that agentive must keep.\n"
OVERRIDE = 'extends: "agentive:rubric@2"\nhuman_if_score_gt: 20\n'
OVERRIDE_PATH = ".agents/custom/.agents/rubric.yaml"
STAMP = re.compile(r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ")
VERSIONISH = re.compile(r'(pinned_ref"?:\s*)"?v?[\d.]+"?')
BAD_REPORT = ("AGENTIVE_DRIFT", "AGENTIVE_CONFLICT", "AGENTIVE_ERROR", "AGENTIVE_STOP")


def releases():
    with open(os.path.join(HERE, "releases.json"), encoding="utf-8") as f:
        return json.load(f)["releases"]


def git_root(*args):
    r = subprocess.run(["git", "-C", ROOT] + list(args), capture_output=True, text=True)
    return r.returncode, r.stdout.strip(), r.stderr.strip()


def have_ref(ref):
    return git_root("cat-file", "-e", ref + "^{commit}")[0] == 0


def extract_payload(ref, dest):
    """The payload of release `ref` under dest; returns the payload directory."""
    p = subprocess.run(f"git -C {ROOT} archive {ref} {PAYLOAD_PATH} | tar -x -C {dest}", shell=True, capture_output=True, text=True)
    if p.returncode != 0:
        raise AssertionError(f"git archive {ref}: {p.stderr}")
    return os.path.join(dest, PAYLOAD_PATH)


def norm(snap_repo):
    """path -> text with timestamps and version pins blanked (the expected differences between releases)."""
    out = {}
    for base, dirs, files in os.walk(snap_repo):
        rb = os.path.relpath(base, snap_repo).replace(os.sep, "/")
        dirs[:] = [d for d in dirs if not (d == ".git" and rb == ".") and not (rb == ".agents" and d == "state")]
        for f in files:
            p = os.path.join(base, f)
            rel = os.path.relpath(p, snap_repo).replace(os.sep, "/")
            with open(p, "rb") as fh:
                data = fh.read()
            try:
                out[rel] = VERSIONISH.sub(r"\1X", STAMP.sub("T", data.decode("utf-8")))
            except UnicodeDecodeError:
                out[rel] = data
    return out


class UpgradeMatrixTest(unittest.TestCase):
    maxDiff = None

    def new_repo(self, name):
        d = tempfile.mkdtemp(prefix=f"agentive-upgrade-{name}-")
        self.addCleanup(shutil.rmtree, d, True)
        T.copy_fixture("py", d)
        T.git(d, "init", "-q", "-b", "main")
        T.git(d, "add", "-A")
        T.git(d, "commit", "-q", "-m", "fixture")
        return d

    def user_edits(self, repo):
        with open(os.path.join(repo, "README.md"), "a", encoding="utf-8") as f:
            f.write(USER_README)
        os.makedirs(os.path.dirname(os.path.join(repo, OVERRIDE_PATH)), exist_ok=True)
        with open(os.path.join(repo, OVERRIDE_PATH), "w", encoding="utf-8") as f:
            f.write(OVERRIDE)

    def old_install(self, rel):
        if not have_ref(rel["ref"]):
            if os.environ.get("CI"):
                self.fail(f"release {rel['version']} ({rel['ref']}) is not in this clone: CI must check out with fetch-depth: 0")
            self.skipTest(f"release {rel['version']} ({rel['ref']}) is not in this clone (shallow or no git history)")
        tmp = tempfile.mkdtemp(prefix="agentive-release-")
        self.addCleanup(shutil.rmtree, tmp, True)
        payload = extract_payload(rel["ref"], tmp)
        repo = self.new_repo(rel["version"])
        rc, out = T.engine(payload, "init", "--repo", repo, "--no-managed-review-copy")
        self.assertEqual(rc, 0, f"release {rel['version']} init: {out[-1500:]}")
        return repo

    def test_release_list_is_not_empty_and_unique(self):
        rs = releases()
        self.assertTrue(rs)
        self.assertEqual(len({r["version"] for r in rs}), len(rs))
        self.assertEqual(len({r["ref"] for r in rs}), len(rs))

    def test_every_release_commit_is_listed(self):
        """The version step makes a `Release vX.Y.Z` commit; a release other than the current one must be in releases.json."""
        rc, out, err = git_root("log", "--format=%h %s", "--grep=^Release v")
        if rc != 0 or not out:
            self.skipTest("no git history with Release commits in this clone")
        with open(os.path.join(ROOT, "package.json"), encoding="utf-8") as f:
            current = json.load(f)["version"]
        listed = {r["version"] for r in releases()}
        missing = []
        for line in out.splitlines():
            sha, subject = line.split(" ", 1)
            v = re.match(r"Release v(\d+\.\d+\.\d+\S*)", subject)
            if v and v.group(1) != current and v.group(1) not in listed:
                missing.append(f"{v.group(1)} ({sha})")
        self.assertEqual(missing, [], "add these releases to tests/upgrade_matrix/releases.json (the file explains how)")

    def test_upgrade_from_every_release(self):
        for rel in releases():
            with self.subTest(release=rel["version"]):
                repo = self.old_install(rel)
                self.user_edits(repo)
                T.git(repo, "add", "-A")
                T.git(repo, "commit", "-q", "-m", "old install + user edits")
                rc, out = T.engine(T.PAYLOADS["claude"], "update", "--repo", repo)
                self.assertEqual(rc, 0, out[-2500:])
                T.changed(out)  # has an AGENTIVE_CHANGED: line
                bad = [ln for ln in out.splitlines() if ln.startswith(BAD_REPORT)]
                self.assertEqual(bad, [], "drift or conflict report on untouched managed regions")
                with open(os.path.join(repo, ".agents/state/drift-report.json"), encoding="utf-8") as f:
                    drift = json.load(f)
                self.assertEqual(drift["items"], [], "untouched managed files reported as user-modified")
                self.assertFalse(drift.get("errors"), "update listed errors")
                self.assertTrue(drift["overwritten"], "update replaced nothing")
                with open(os.path.join(repo, "README.md"), encoding="utf-8") as f:
                    self.assertTrue(f.read().endswith(USER_README), "unmanaged user edit lost")
                with open(os.path.join(repo, OVERRIDE_PATH), encoding="utf-8") as f:
                    self.assertEqual(f.read(), OVERRIDE, "override in .agents/custom/ changed")
                rc, st = T.engine(T.PAYLOADS["claude"], "status", "--repo", repo)
                self.assertEqual(rc, 0, st[-1500:])
                self.assertIn("custom_overrides=yes (1)", st, "status no longer sees the override in .agents/custom/")
                after = T.snapshot(repo)
                rc, out2 = T.engine(T.PAYLOADS["claude"], "update", "--repo", repo)
                self.assertEqual(rc, 0, out2[-2000:])
                self.assertEqual(T.changed(out2), [], f"second update changed files\n{out2[-1500:]}")
                self.assertEqual(T.snapshot(repo), after, "bytes changed on the second update")
                fresh = self.new_repo("fresh-" + rel["version"])
                rc, o = T.engine(T.PAYLOADS["claude"], "init", "--repo", fresh, "--no-managed-review-copy")
                self.assertEqual(rc, 0, o[-1500:])
                self.user_edits(fresh)
                rc, o = T.engine(T.PAYLOADS["claude"], "update", "--repo", fresh)
                self.assertEqual(rc, 0, o[-1500:])
                a, b = norm(repo), norm(fresh)
                self.assertEqual(sorted(a), sorted(b), "files differ from a fresh init")
                self.assertEqual([k for k in a if a[k] != b[k]], [], "content differs from a fresh init (beyond timestamps and version pins)")

    def test_deleted_managed_files_are_reported_and_accept_restores(self):
        """A managed file deleted after the install counts as the user's choice (drift item, reason customized, kept deleted);
        `--accept <id>` brings it back from the current template."""
        repo = self.old_install(releases()[-1])
        lost = [".agents/hooks/stop-verify.py", ".agents/bin/agentive-review"]
        for p in lost:
            os.remove(os.path.join(repo, p))
        rc, out = T.engine(T.PAYLOADS["claude"], "update", "--repo", repo)
        self.assertEqual(rc, 0, out[-2500:])
        for p in lost:
            self.assertIn(f"AGENTIVE_DRIFT id={p} reason=customized", out)
            self.assertFalse(os.path.exists(os.path.join(repo, p)), f"{p} came back without --accept")
        rc, out = T.engine(T.PAYLOADS["claude"], "update", "--repo", repo, "--accept", *lost)
        self.assertEqual(rc, 0, out[-2500:])
        for p in lost:
            self.assertTrue(os.path.isfile(os.path.join(repo, p)), f"--accept did not restore {p}")
        rc, out = T.engine(T.PAYLOADS["claude"], "update", "--repo", repo)
        self.assertEqual(T.changed(out), [])

    def test_interrupted_install_is_completed_by_init(self):
        """compound-engineering 487c5e0 style: the install stopped before the manifest was written; init run again completes it."""
        repo = self.old_install(releases()[-1])
        for p in (".agents/manifest.yaml", ".agents/skills.lock", ".agents/hooks/stop-verify.py"):
            os.remove(os.path.join(repo, p))
        rc, out = T.engine(T.PAYLOADS["claude"], "init", "--repo", repo, "--no-managed-review-copy")
        self.assertEqual(rc, 0, out[-2500:])
        for p in (".agents/manifest.yaml", ".agents/skills.lock", ".agents/hooks/stop-verify.py"):
            self.assertTrue(os.path.isfile(os.path.join(repo, p)), f"init did not recreate {p}")
        rc, out = T.engine(T.PAYLOADS["claude"], "update", "--repo", repo)
        self.assertEqual(rc, 0, out[-2500:])


if __name__ == "__main__":
    unittest.main()
