#!/usr/bin/env python3
# agentive-unit: selftest.sample_repo_agent_run
# design: spec.framework_repo.self_tests (sample_repo_tests agent_run), spec.context_management.headless.standard_cmd, spec.context_management.headless.forbid_bare_for
"""Agent-run half of sample_repo_tests (needs ANTHROPIC_API_KEY; CI workflow .github/workflows/selftest-agent-run.yml).

For each stack (default ts and py [설계 판단: 1-2 stacks]) it copies the fixture into a temporary git repo, then runs
fixture tasks through the headless standard command (AGENTIVE_HEADLESS=1 claude -p ... --permission-mode dontAsk,
never --bare) with the generated Claude Code plugin loaded from plugins/claude-code/agentive:
  1 `/agentive:init --no-managed-review-copy`: the agentive manifest exists and validates, the test verb is derived
  2 a small code task: the agent adds a function with a test and runs `.agents/bin/verb test`; the check command passes
Usage: python3 tests/sample_repo/agent_run.py [--stacks ts py] [--budget-usd 3] [--dry-run]
Exit 0 all tasks passed, 1 a task failed, 2 setup error. AGENTIVE_CLAUDE overrides the claude binary.
미검증: `claude --plugin-dir <dir>` loading the plugin for a headless run.
"""
import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, HERE)
from test_init_apply import copy_fixture, git, schemas  # noqa: E402

PLUGIN = os.path.join(ROOT, "plugins", "claude-code", "agentive")
TOOLS = "Read,Edit,Write,Glob,Grep,Bash(.agents/bin/verb *),Bash(git add *),Bash(git commit *),Bash(python3 *),Bash(npm *),Bash(uv *)"
TASKS = {
    "ts": {"prompt": "Add an exported function `sum(xs: number[]): number` to src/math.ts and a test for it in test/math.test.ts, "
                     "then run `.agents/bin/verb test` and fix any failure. Do not commit.",
           "check": ["grep", "-q", "export function sum", "src/math.ts"]},
    "py": {"prompt": "Add a function `count_words(text: str) -> int` to src/fixture_py/__init__.py and a test for it in "
                     "tests/test_slugify.py, then run `.agents/bin/verb test` and fix any failure. Do not commit.",
           "check": ["grep", "-q", "def count_words", "src/fixture_py/__init__.py"]},
    "go": {"prompt": "Add `func Max(xs []float64) (float64, error)` to calc/calc.go with a test, then run `.agents/bin/verb test`. Do not commit.",
           "check": ["grep", "-q", "func Max", "calc/calc.go"]},
    "java": {"prompt": "Add `public static boolean isBlank(String s)` to Strings.java with a test, then run `.agents/bin/verb test`. Do not commit.",
             "check": ["grep", "-rq", "isBlank", "src/main/java"]},
}


def headless(repo, prompt, budget, dry):
    cmd = [os.environ.get("AGENTIVE_CLAUDE", "claude"), "-p", prompt, "--plugin-dir", PLUGIN, "--permission-mode", "dontAsk",
           "--allowedTools", TOOLS, "--output-format", "json", "--max-budget-usd", str(budget)]
    if dry:
        print("DRY", repo, " ".join(cmd))
        return 0, ""
    env = dict(os.environ, AGENTIVE_HEADLESS="1", CLAUDE_CODE_RETRY_WATCHDOG="1")
    r = subprocess.run(cmd, cwd=repo, env=env, capture_output=True, text=True, timeout=1800)
    return r.returncode, (r.stdout + r.stderr)[-4000:]


def run_stack(stack, budget, dry):
    repo = tempfile.mkdtemp(prefix=f"agentive-agent-{stack}-")
    try:
        copy_fixture(stack, repo)
        git(repo, "init", "-q", "-b", "main")
        git(repo, "add", "-A")
        git(repo, "commit", "-q", "-m", "fixture")
        problems = []
        rc, out = headless(repo, "/agentive:init --no-managed-review-copy", budget, dry)
        if dry:
            headless(repo, TASKS[stack]["prompt"], budget, dry)
            return []
        manifest = os.path.join(repo, ".agents", "manifest.yaml")
        if not os.path.exists(manifest):
            return [f"{stack}: /agentive:init wrote no agentive manifest (rc={rc}): {out}"]
        import yaml
        doc = yaml.safe_load(open(manifest, encoding="utf-8"))
        problems += [f"{stack}: manifest: {e}" for e in schemas()("manifest", doc)]
        if (doc.get("verbs") or {}).get("test", {}).get("status") == "pending":
            problems.append(f"{stack}: the test verb is still pending after init")
        rc, out = headless(repo, TASKS[stack]["prompt"], budget, dry)
        if subprocess.run(TASKS[stack]["check"], cwd=repo).returncode != 0:
            problems.append(f"{stack}: task check {TASKS[stack]['check']} failed (rc={rc}): {out}")
        verify = os.path.join(repo, ".agents", "state", "verify.json")
        if not os.path.exists(verify) or json.load(open(verify, encoding="utf-8")).get("total_failures", 1) != 0:
            problems.append(f"{stack}: .agents/state/verify.json missing or failing after the task")
        return problems
    finally:
        shutil.rmtree(repo, ignore_errors=True)


def main(argv):
    ap = argparse.ArgumentParser()
    ap.add_argument("--stacks", nargs="+", default=["ts", "py"], choices=sorted(TASKS))
    ap.add_argument("--budget-usd", type=float, default=3)
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args(argv)
    if not os.path.isdir(PLUGIN):
        print("AGENT_RUN=error plugin not generated; NEXT=python3 tools/generate.py")
        return 2
    problems = []
    for s in a.stacks:
        problems += run_stack(s, a.budget_usd, a.dry_run)
    for p in problems:
        print("FAIL", p)
    print(f"AGENT_RUN={'ok' if not problems else 'fail'} stacks={','.join(a.stacks)}; NEXT=" +
          ("none" if not problems else "read the FAIL lines; fix the engine, templates or skills they name"))
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
