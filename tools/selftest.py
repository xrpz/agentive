#!/usr/bin/env python3
# agentive-unit: gen.selftest
# design: spec.framework_repo.self_tests, spec.framework_repo.versioning.check
"""Run the framework self-tests (FM.selftest): the same steps locally and in CI.

  python3 tools/selftest.py                 every step, in order; exit 1 if any fails (CI, a release, before a PR)
  python3 tools/selftest.py <step> [...]    only the named steps
  python3 tools/selftest.py --fast          before a commit: the cheap steps plus the tests of the areas the change touched
  python3 tools/selftest.py --plan          print what --fast would run and why; run nothing

Steps (ids from design spec.framework_repo.self_tests.items, plus the ones marked [설계 판단]):
  generate_check      tools/generate.py --check (writes nothing)
  schema_validation   tests/design/validate_schemas.py on design/schemas
  size_budget         limits from the vendors' pages on the installed AGENTS.md, the skill descriptions and this repo's AGENTS.md,
                      and that .claude/settings.json denies Edit on every generated output (tests/budget/) [설계 판단]
  component_tests     unittest discovery of tests/<area>/test_*.py except sample_repo, design and budget [설계 판단]; the areas run as
                      separate processes at the same time (at most os.cpu_count()), reported in sorted order with each time
  sample_repo_tests   deterministic init/apply tests on fixtures/{ts,py,go,java} (tests/sample_repo/, owner D)
  version_sync_check  package.json version == both plugin manifests == Codex skills.lock pinned_ref (== CHANGELOG.md head) [S50]

--fast [설계 판단, decisions.accepted[SELFTESTFAST]] runs generate_check, schema_validation, size_budget and version_sync_check
always, and component_tests only for the areas the changed files belong to (changed = staged, unstaged and untracked files
against HEAD; generated outputs follow from their sources and are not mapped). It falls back to the full run when a changed path
is under src/repo-layer/, src/engine/, src/plugin/, src/units/ or design/, or matches no rule in FAST_RULES (an unknown path).
Output: one `SELFTEST <step> ok|FAIL|SKIP (<seconds>s)` line per step, then `SELFTEST: ok (<total>)` or
`SELFTEST: FAIL <steps>; NEXT: ...`. A fast run says so on its last line: only the full run (no flag) is what CI and a release run.
"""
import concurrent.futures
import json
import os
import re
import subprocess
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
# True: a missing tests/sample_repo/test_*.py fails the sample_repo_tests step instead of skipping it (IMPLEMENTATION.md §6).
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


NON_COMPONENT_AREAS = ("sample_repo", "design", "budget", "__pycache__")   # each has its own step


def component_areas():
    return sorted(d for d in os.listdir(os.path.join(ROOT, "tests"))
                  if os.path.isdir(os.path.join(ROOT, "tests", d)) and d not in NON_COMPONENT_AREAS)


# Areas that take longest start first, so the last one to finish is not a long one started late [설계 판단]; the report order is
# always the sorted area order. An area not listed starts after these.
SLOWEST_FIRST = ("engine", "review", "content", "hooks", "generator")


def _run_area(area):
    t0 = time.monotonic()
    rc, out = _discover(os.path.join("tests", area))
    return rc, out, time.monotonic() - t0


def step_component_tests(areas=None, jobs=None):
    """areas: tests/<area> names to run; None = all of them (a name that is not an area is an error, never a silent skip).
    The areas run as separate processes at the same time, at most `jobs` (default os.cpu_count()) at once; they share no state
    (each test makes its own temporary directories). The message lists the areas in sorted order with each one's time."""
    known = component_areas()
    unknown = [a for a in (areas or []) if a not in known]
    if unknown:
        return False, f"no such test area {unknown}; known: {known}"
    todo = [a for a in (known if areas is None else [x for x in known if x in areas]) if _test_files(os.path.join("tests", a))]
    start = sorted(todo, key=lambda a: (SLOWEST_FIRST.index(a) if a in SLOWEST_FIRST else len(SLOWEST_FIRST), a))
    done = {}
    if start:
        with concurrent.futures.ThreadPoolExecutor(max_workers=max(1, min(jobs or os.cpu_count() or 1, len(start)))) as pool:
            futures = {a: pool.submit(_run_area, a) for a in start}
            done = {a: f.result() for a, f in futures.items()}
    msgs, ok = [], True
    for a in todo:
        rc, out, secs = done[a]
        ok = ok and rc == 0
        msgs.append(f"tests/{a}: " + (out.splitlines()[-1] if out else "no output") + f" ({fmt_time(secs)})" if rc == 0
                    else f"tests/{a} ({fmt_time(secs)}):\n{tail(out)}")
    return ok, "\n".join(msgs) or "no component tests"


def step_size_budget():
    rc, out = _discover(os.path.join("tests", "budget"))
    sizes = [ln for ln in out.splitlines() if ln.startswith("SIZE ")]
    return rc == 0, "\n".join(sizes + [out.splitlines()[-1]]) if rc == 0 else tail(out)


def step_sample_repo_tests():
    if not _test_files(os.path.join("tests", "sample_repo")):
        if REQUIRE_SAMPLE_REPO_TESTS:
            return False, "tests/sample_repo/ has no test_*.py but REQUIRE_SAMPLE_REPO_TESTS is True"
        return None, "no tests yet (add tests/sample_repo/test_*.py and keep REQUIRE_SAMPLE_REPO_TESTS = True)"
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
    "size_budget": (step_size_budget, "shorten the installed AGENTS.md text or the skill descriptions the SIZE lines name (limits: tests/budget/test_size_budget.py); for a missing deny rule add the named Edit(...) line to permissions.deny in .claude/settings.json (confirm path: ask the author first)"),
    "component_tests": (step_component_tests, "fix the failing tests/<area> test or the code it covers"),
    "sample_repo_tests": (step_sample_repo_tests, "fix the engine or templates the failing fixture test names"),
    "version_sync_check": (step_version_sync_check, "never edit versions by hand: npm run version (changesets), or python3 tools/generate.py"),
}

# ---------------------------------------------------------------- --fast: which steps a change needs [설계 판단]
FAST_ALWAYS = ["generate_check", "schema_validation", "size_budget", "version_sync_check"]
# A change under one of these prefixes can break tests outside its own area: the engine, the templates and the design are
# installed or read by the engine, review, content and hooks tests alike (they install into temporary copies), so run everything.
FULL_PREFIXES = ("src/repo-layer/", "src/engine/", "src/plugin/", "src/units/", "design/")
# Generated outputs follow from their sources and the always-run steps compare them (generate_check); versions and the
# changesets are covered by version_sync_check.
GENERATED = ("plugins/", "docs/map/", "docs/DECISIONS.md", "design/INDEX.md", ".claude-plugin/marketplace.json",
             ".agents/plugins/marketplace.json", "CHANGELOG.md", "package.json", "package-lock.json", ".changeset/")
# first matching rule wins: (path prefix or exact path, areas of tests/ to run, or "sample_repo" for the fixture step)
FAST_RULES = [
    ("tests/sample_repo/", ["sample_repo"]),
    ("fixtures/", ["sample_repo"]),
    ("tests/design/", []),
    ("tests/budget/", []),
    ("tests/", None),            # tests/<area>/...: that area (resolved below)
    ("tools/", ["generator"]),
    ("docs/", ["generator"]),
    ("README.md", ["content", "generator"]),
    ("AGENTS.md", ["content", "generator"]),
    ("framework.yaml", ["generator"]),
    (".github/workflows/", ["ci"]),
    (".gitignore", []),
    ("LICENSE", []),
]


def changed_paths():
    """Staged, unstaged and untracked paths against HEAD, or None when git cannot say (then the caller runs everything)."""
    paths = set()
    for cmd in (["git", "diff", "--name-only", "HEAD"], ["git", "ls-files", "--others", "--exclude-standard"]):
        r = subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True)
        if r.returncode != 0:
            return None
        paths.update(ln.strip() for ln in r.stdout.splitlines() if ln.strip())
    return sorted(paths)


def plan(paths):
    """-> (steps, areas or None for all, reason). `steps` keeps the order of STEPS; the reason says why the run is full."""
    if paths is None:
        return list(STEPS), None, "full: git could not list the changed files"
    wanted, areas, known = set(FAST_ALWAYS), set(), component_areas()
    for path in paths:
        if any(path.startswith(g) for g in GENERATED):
            continue
        if path.startswith(FULL_PREFIXES):
            return list(STEPS), None, f"full: {path} is under {next(p for p in FULL_PREFIXES if path.startswith(p))}"
        for prefix, rule in FAST_RULES:
            if path == prefix or path.startswith(prefix):
                if rule is None:
                    area = path.split("/")[1] if path.count("/") >= 2 else ""
                    if area not in known:
                        return list(STEPS), None, f"full: {path} is in no test area"
                    areas.add(area)
                elif rule == ["sample_repo"]:
                    wanted.add("sample_repo_tests")
                else:
                    areas.update(rule)
                break
        else:
            return list(STEPS), None, f"full: no rule for {path}"
    if areas:
        wanted.add("component_tests")
    return [s for s in STEPS if s in wanted], sorted(areas), f"fast: {len(paths)} changed path(s)"


def fmt_time(sec):
    return f"{sec:.1f}s" if sec < 60 else f"{int(sec // 60)}m{int(sec % 60):02d}s"


def main(argv):
    fast = "--fast" in argv
    plan_only = "--plan" in argv
    names = [a for a in argv if not a.startswith("--")]
    flags = [a for a in argv if a.startswith("--") and a not in ("--fast", "--plan")]
    if flags or (names and (fast or plan_only)):
        print(f"SELFTEST: unknown option(s) or steps mixed with --fast/--plan {flags + names}; usage: selftest.py [<step> ...] | --fast | --plan")
        return 2
    areas = None
    if fast or plan_only:
        steps, areas, why = plan(changed_paths())
        names = steps
        print(f"SELFTEST plan: {why}; steps: {', '.join(steps)}" + (f"; component areas: {', '.join(areas)}" if areas is not None and "component_tests" in steps else ""))
        if plan_only:
            return 0
    names = names or list(STEPS)
    unknown = [n for n in names if n not in STEPS]
    if unknown:
        print(f"SELFTEST: unknown step(s) {unknown}; known: {list(STEPS)}")
        return 2
    failed, started = [], time.monotonic()
    for n in names:
        fn, fix = STEPS[n]
        t0 = time.monotonic()
        ok, msg = fn(areas) if n == "component_tests" else fn()
        status = "SKIP" if ok is None else ("ok" if ok else "FAIL")
        print(f"SELFTEST {n} {status} ({fmt_time(time.monotonic() - t0)})")
        if msg:
            print("  " + msg.replace("\n", "\n  "))
        if ok is False:
            failed.append((n, fix))
    total = fmt_time(time.monotonic() - started)
    if failed:
        print("SELFTEST: FAIL " + ", ".join(n for n, _ in failed) + f" ({total}); NEXT: " + " | ".join(f"{n}: {fx}" for n, fx in failed))
        return 1
    if fast and not why.startswith("full"):
        print(f"SELFTEST: ok (fast subset, {total}); CI and a release run the full set: python3 tools/selftest.py")
    else:
        print(f"SELFTEST: ok ({total})")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
