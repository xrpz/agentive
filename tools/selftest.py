#!/usr/bin/env python3
# agentive-unit: gen.selftest
# design: spec.framework_repo.self_tests, spec.framework_repo.versioning.check
"""Run the framework self-tests (FM.selftest): the same steps locally and in CI.

  python3 tools/selftest.py                 every step, in order; exit 1 if any fails
  python3 tools/selftest.py <step> [...]    only the named steps

Steps (ids from design spec.framework_repo.self_tests.items, plus two marked [설계 판단]):
  generate_check      tools/generate.py --check (writes nothing)
  schema_validation   tests/design/validate_schemas.py on design/schemas
  component_tests     unittest discovery of tests/<area>/test_*.py except tests/sample_repo/ [설계 판단]
  sample_repo_tests   deterministic init/apply tests on fixtures/{ts,py,go,java} (tests/sample_repo/, owner D)
  version_sync_check  package.json version == both plugin manifests == Codex skills.lock pinned_ref (== CHANGELOG.md head) [S50]
Output: one `SELFTEST <step> ok|FAIL|SKIP` line per step, then `SELFTEST: ok` or `SELFTEST: FAIL <steps>; NEXT: ...`.
"""
import json
import os
import re
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
# D sets this to True once tests/sample_repo/ holds the deterministic init/apply tests (IMPLEMENTATION.md §7 D).
REQUIRE_SAMPLE_REPO_TESTS = True


def run(cmd):
    r = subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True)
    out = (r.stdout + r.stderr).strip()
    return r.returncode, out


def tail(out, n=40):
    lines = out.splitlines()
    return "\n".join(lines[-n:])


def step_generate_check():
    rc, out = run([sys.executable, "tools/generate.py", "--check"])
    return rc == 0, tail(out)


def step_schema_validation():
    rc, out = run([sys.executable, "-I", "tests/design/validate_schemas.py", "design/schemas"])
    bad = [ln for ln in out.splitlines() if " !! " in ln or ln.startswith("FAIL")]
    return rc == 0, (out.splitlines()[-1] if rc == 0 else "\n".join(bad[:40] + [out.splitlines()[-1]]))


def _discover(start, top=None):
    cmd = [sys.executable, "-m", "unittest", "discover", "-s", start, "-p", "test_*.py"]
    if top:
        cmd += ["-t", top]
    return run(cmd)


def _test_files(d):
    hits = []
    for base, dirs, files in os.walk(os.path.join(ROOT, d)):
        dirs[:] = [x for x in dirs if x != "__pycache__"]
        hits += [os.path.join(base, f) for f in files if f.startswith("test_") and f.endswith(".py")]
    return hits


def step_component_tests():
    areas = sorted(d for d in os.listdir(os.path.join(ROOT, "tests"))
                   if os.path.isdir(os.path.join(ROOT, "tests", d)) and d not in ("sample_repo", "design", "__pycache__"))
    msgs, ok = [], True
    for a in areas:
        if not _test_files(os.path.join("tests", a)):
            continue
        rc, out = _discover(os.path.join("tests", a))
        ok = ok and rc == 0
        msgs.append(f"tests/{a}: " + (out.splitlines()[-1] if out else "no output") if rc == 0 else f"tests/{a}:\n{tail(out)}")
    return ok, "\n".join(msgs) or "no component tests"


def step_sample_repo_tests():
    if not _test_files(os.path.join("tests", "sample_repo")):
        if REQUIRE_SAMPLE_REPO_TESTS:
            return False, "tests/sample_repo/ has no test_*.py but REQUIRE_SAMPLE_REPO_TESTS is True"
        return None, "no tests yet (owner D fills tests/sample_repo/, then sets REQUIRE_SAMPLE_REPO_TESTS = True)"
    rc, out = _discover(os.path.join("tests", "sample_repo"))
    return rc == 0, tail(out)


def step_version_sync_check():
    with open(os.path.join(ROOT, "package.json"), encoding="utf-8") as f:
        version = json.load(f)["version"]
    found = {}
    for p in ("plugins/claude-code/agentive/.claude-plugin/plugin.json", "plugins/codex/agentive/plugin.json"):
        try:
            with open(os.path.join(ROOT, p), encoding="utf-8") as f:
                found[p] = json.load(f).get("version")
        except FileNotFoundError:
            found[p] = None
    try:
        with open(os.path.join(ROOT, "plugins/codex/agentive/skills.lock"), encoding="utf-8") as f:
            for name, e in json.load(f)["skills"].items():
                found[f"plugins/codex/agentive/skills.lock {name}.pinned_ref"] = e.get("pinned_ref", "").lstrip("v")
    except FileNotFoundError:
        found["plugins/codex/agentive/skills.lock"] = None
    cl = os.path.join(ROOT, "CHANGELOG.md")
    if os.path.exists(cl):
        with open(cl, encoding="utf-8") as f:
            m = re.search(r"^## (\d+\.\d+\.\d+\S*)", f.read(), re.M)
        found["CHANGELOG.md latest heading"] = m.group(1) if m else None
    bad = [f"{p}: {v!r} != package.json {version!r}" for p, v in found.items() if v != version]
    return not bad, ("\n".join(bad) if bad else f"version {version} in sync ({len(found)} places)")


STEPS = {
    "generate_check": (step_generate_check, "python3 tools/generate.py, review the diff, commit it"),
    "schema_validation": (step_schema_validation, "fix design/schemas or the matching check in tests/design/validate_schemas.py"),
    "component_tests": (step_component_tests, "fix the failing tests/<area> test or the code it covers"),
    "sample_repo_tests": (step_sample_repo_tests, "fix the engine or templates the failing fixture test names"),
    "version_sync_check": (step_version_sync_check, "never edit versions by hand: npm run version (changesets), or python3 tools/generate.py"),
}


def main(argv):
    names = argv or list(STEPS)
    unknown = [n for n in names if n not in STEPS]
    if unknown:
        print(f"SELFTEST: unknown step(s) {unknown}; known: {list(STEPS)}")
        return 2
    failed = []
    for n in names:
        fn, fix = STEPS[n]
        ok, msg = fn()
        status = "SKIP" if ok is None else ("ok" if ok else "FAIL")
        print(f"SELFTEST {n} {status}")
        if msg:
            print("  " + msg.replace("\n", "\n  "))
        if ok is False:
            failed.append((n, fix))
    if failed:
        print("SELFTEST: FAIL " + ", ".join(n for n, _ in failed) + "; NEXT: " + " | ".join(f"{n}: {fx}" for n, fx in failed))
        return 1
    print("SELFTEST: ok")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
