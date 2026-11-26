#!/usr/bin/env python3
# agentive-unit: gen.plugin_eval
# design: decisions.accepted[PLUGINEVAL], decisions.accepted[MEASUREDWITH], spec.framework_repo
"""Opt-in eval of agentive itself with `claude plugin eval` and a no-plugin baseline (decisions.accepted[PLUGINEVAL]).

  python3 tools/plugin_eval.py build [--out DIR]    assemble the throw-away eval plugin (no model call); prints its directory
  python3 tools/plugin_eval.py check [--out DIR]    build, then check the suite against the official case format (no model call)
  python3 tools/plugin_eval.py run [--out DIR] [-- <extra claude plugin eval options>]
                                                    build, then `claude plugin eval <dir> --scaffold --trust-plugin ...`: real model
                                                    calls on your account (cases x runs x 2 arms plus judge calls); never run by selftest
  python3 tools/plugin_eval.py command [--out DIR]  print the command `run` would execute

Why a throw-away plugin: `claude plugin eval` loads only the plugin (no project-level .claude/, CLAUDE.md or .mcp.json, official page
'How runs are isolated'), but what agentive adds in a user repo is exactly that project layer. The assembled directory is
  generated Claude Code plugin (plugins/claude-code/agentive)
  + skills/<name>   the rendered repo skills of a fixture install, as plugin skills
  + hooks/hooks.json  the plugin's own hook + the hooks of that install's .claude/settings.json (they use ${CLAUDE_PROJECT_DIR})
  + evals/<case>    tools/plugin_eval/cases, with the installed AGENTS.md as each case's append_system_prompt
  + evals/_fixture  fixtures/py, copied into every workspace by the case's scaffold script (which also runs the engine's init)
Nothing is written into plugins/ and nothing ships to users. Pins (models, threshold, cost ceiling): tools/plugin_eval/config.json.
Results: <out>/agentive-eval/evals/results/<timestamp>/ (report.html, aggregate-result.json) and <out>/result.json (--json); quote a
result with its claudeVersion and the pinned model (MEASUREDWITH). Needs Claude Code >= 2.1.269, git >= 2.31, python3 >= 3.13, uv (the
fixture's test command), bubblewrap and socat on Linux (the Bash grant runs under the sandbox).
How a result decides a shrink item: docs/IMPLEMENTATION.md section 6 (plugin_eval).
"""
import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PLUGIN_SRC = os.path.join(ROOT, "plugins", "claude-code", "agentive")
CASES = os.path.join(ROOT, "tools", "plugin_eval", "cases")
CONFIG = os.path.join(ROOT, "tools", "plugin_eval", "config.json")
FIXTURE = os.path.join(ROOT, "fixtures", "py")
PLUGIN_DIR_NAME = "agentive-eval"
# the official page: an unknown prompt.md key is an error; grader types; the keys of a grader file
PROMPT_KEYS = {"schema_version", "name", "description", "tags", "plugins", "runs", "expected_outcome", "model", "max_turns",
               "timeout_seconds", "allowed_tools", "disallowed_tools", "append_system_prompt", "env"}
GRADER_TYPES = {"regex", "tool_used", "tool_order", "file_exists", "llm", "baseline"}
GRADER_KEYS = {"type", "weight", "arm", "pattern", "flags", "match", "target", "tool", "input_match", "min", "max", "before", "after",
               "path", "exists", "criteria", "focus", "baseline_file"}
TARGET_WORDS = {"last_message", "trace", "files", "mock_calls"}


def unescape(rel):
    return "/".join(("." + c[4:]) if c.startswith("dot-") else c for c in rel.split("/"))


def load_config():
    with open(CONFIG, encoding="utf-8") as f:
        return json.load(f)


def copy_fixture(dest):
    for base, dirs, files in os.walk(FIXTURE):
        dirs[:] = [d for d in dirs if d not in ("node_modules", "__pycache__", ".venv", ".pytest_cache")]
        for f in files:
            rel = os.path.relpath(os.path.join(base, f), FIXTURE).replace(os.sep, "/")
            out = os.path.join(dest, unescape(rel))
            os.makedirs(os.path.dirname(out), exist_ok=True)
            shutil.copy2(os.path.join(base, f), out)


def install_fixture(dest):
    """The repo layer on a copy of fixtures/py with the engine of the generated plugin (what a user's /agentive:init writes)."""
    copy_fixture(dest)
    env = dict(os.environ, GIT_AUTHOR_NAME="e", GIT_AUTHOR_EMAIL="e@example.com", GIT_COMMITTER_NAME="e", GIT_COMMITTER_EMAIL="e@example.com",
               GIT_CONFIG_NOSYSTEM="1", HOME=dest)
    env = {k: v for k, v in env.items() if k not in ("CI", "GITHUB_ACTIONS", "AGENTIVE_HEADLESS")}
    for args in (["init", "-q", "-b", "main"], ["add", "-A"], ["commit", "-q", "-m", "fixture"]):
        r = subprocess.run(["git", "-C", dest] + args, capture_output=True, text=True, env=env)
        if r.returncode != 0:
            raise SystemExit(f"plugin_eval: git {args}: {r.stderr}")
    engine = os.path.join(PLUGIN_SRC, "payload", "engine", "apply.py")
    r = subprocess.run([sys.executable, engine, "init", "--repo", dest, "--no-managed-review-copy"], capture_output=True, text=True, env=env)
    if r.returncode != 0:
        raise SystemExit("plugin_eval: engine init failed:\n" + (r.stdout + r.stderr)[-2000:])


def merge_hooks(plugin_hooks, repo_hooks):
    """{event: [entries]}: the plugin's own entries first, then the repo's."""
    out = {k: list(v) for k, v in (plugin_hooks or {}).items()}
    for event, entries in (repo_hooks or {}).items():
        out.setdefault(event, []).extend(entries)
    return out


def build(out_dir):
    """Assemble <out_dir>/agentive-eval; returns its path. No model call."""
    if not os.path.isfile(os.path.join(PLUGIN_SRC, ".claude-plugin", "plugin.json")):
        raise SystemExit("plugin_eval: plugins/claude-code/agentive is missing: run python3 tools/generate.py")
    plugin = os.path.join(os.path.abspath(out_dir), PLUGIN_DIR_NAME)
    if os.path.exists(plugin):
        shutil.rmtree(plugin)
    shutil.copytree(PLUGIN_SRC, plugin, symlinks=False)
    with tempfile.TemporaryDirectory(prefix="agentive-eval-fixture-") as fx:
        install_fixture(fx)
        with open(os.path.join(fx, ".claude", "settings.json"), encoding="utf-8") as f:
            repo_hooks = json.load(f).get("hooks") or {}
        with open(os.path.join(fx, "AGENTS.md"), encoding="utf-8") as f:
            agents_md = f.read()
        skills_src = os.path.join(fx, ".agents", "skills")
        for name in sorted(os.listdir(skills_src)):
            d = os.path.join(skills_src, name)
            if os.path.isfile(os.path.join(d, "SKILL.md")):
                shutil.copytree(d, os.path.join(plugin, "skills", name), dirs_exist_ok=True)
    hp = os.path.join(plugin, "hooks", "hooks.json")
    with open(hp, encoding="utf-8") as f:
        hooks_doc = json.load(f)
    hooks_doc["hooks"] = merge_hooks(hooks_doc.get("hooks"), repo_hooks)
    hooks_doc["description"] = "eval-only: the plugin hook plus the hooks of an installed agentive repo layer (tools/plugin_eval.py)"
    with open(hp, "w", encoding="utf-8") as f:
        json.dump(hooks_doc, f, indent=2)
        f.write("\n")
    evals = os.path.join(plugin, "evals")
    shutil.copytree(CASES, evals)
    copy_fixture(os.path.join(evals, "_fixture", "py"))
    marker = json.dumps(agents_md)
    for base, _dirs, files in os.walk(evals):
        for fn in files:
            p = os.path.join(base, fn)
            if fn.endswith(".md") or fn.endswith(".sh") or fn.endswith(".yaml"):
                with open(p, encoding="utf-8") as f:
                    text = f.read()
                new = text.replace('"@@AGENTS_MD@@"', marker).replace("@@PLUGIN@@", plugin)
                if new != text:
                    with open(p, "w", encoding="utf-8") as f:
                        f.write(new)
    return plugin


def frontmatter(path):
    with open(path, encoding="utf-8") as f:
        text = f.read()
    m = re.match(r"---\n(.*?)\n---\n?(.*)", text, re.S)
    if not m:
        return None, text
    import yaml  # dev-time only (PyYAML, requirements-dev.txt)
    return yaml.safe_load(m.group(1)) or {}, m.group(2)


def check_suite(plugin):
    """Problems of the assembled suite against the official case format (page 'Eval suite reference'); [] when it is loadable."""
    problems = []
    evals = os.path.join(plugin, "evals")
    cases = sorted(d for d in os.listdir(evals) if os.path.isfile(os.path.join(evals, d, "prompt.md")) or os.path.isfile(os.path.join(evals, d, "case.yaml")))
    if not cases:
        return ["no case under evals/"]
    for c in cases:
        cdir = os.path.join(evals, c)
        try:
            fm, body = frontmatter(os.path.join(cdir, "prompt.md"))
        except Exception as e:  # noqa: BLE001
            problems.append(f"{c}/prompt.md: frontmatter does not parse ({e})")
            continue
        if fm is None:
            problems.append(f"{c}/prompt.md: no frontmatter")
            continue
        for k in sorted(set(fm) - PROMPT_KEYS):
            problems.append(f"{c}/prompt.md: unknown key {k}")
        if not body.strip():
            problems.append(f"{c}/prompt.md: empty prompt")
        if "@@" in json.dumps(fm):
            problems.append(f"{c}/prompt.md: a placeholder was not filled")
        if not fm.get("append_system_prompt"):
            problems.append(f"{c}/prompt.md: append_system_prompt (the project context stand-in) is empty")
        for key, lo, hi in (("max_turns", 1, 200), ("timeout_seconds", 1, 3600), ("runs", 1, 50)):
            if key in fm and not (isinstance(fm[key], int) and lo <= fm[key] <= hi):
                problems.append(f"{c}/prompt.md: {key} must be {lo}..{hi}")
        gdir = os.path.join(cdir, "graders")
        graders = sorted(os.listdir(gdir)) if os.path.isdir(gdir) else []
        if not graders:
            problems.append(f"{c}: no grader (a case without one fails to load)")
        paid = 0
        for g in graders:
            gfm, gbody = frontmatter(os.path.join(gdir, g))
            if gfm is None or gfm.get("type") not in GRADER_TYPES:
                problems.append(f"{c}/graders/{g}: type must be one of {sorted(GRADER_TYPES)}")
                continue
            for k in sorted(set(gfm) - GRADER_KEYS):
                problems.append(f"{c}/graders/{g}: unknown key {k}")
            t = gfm["type"]
            paid += t in ("llm", "baseline")
            if t == "regex":
                if not gfm.get("pattern"):
                    problems.append(f"{c}/graders/{g}: regex needs pattern")
                else:
                    if "(?" in gfm["pattern"] and re.search(r"\(\?[a-z]+\)", gfm["pattern"]):
                        problems.append(f"{c}/graders/{g}: inline flags such as (?s) are not supported (use flags:)")
                tg = gfm.get("target", "last_message")
                if not ((isinstance(tg, str) and tg in TARGET_WORDS) or (isinstance(tg, dict) and tg.get("source") == "file" and tg.get("path"))):
                    problems.append(f"{c}/graders/{g}: bad target {tg!r}")
            if t == "tool_used" and not gfm.get("tool"):
                problems.append(f"{c}/graders/{g}: tool_used needs tool")
            if t == "llm" and not (gfm.get("criteria") or gbody.strip()):
                problems.append(f"{c}/graders/{g}: llm needs criteria")
        if graders and paid == len(graders):
            problems.append(f"{c}: only paid graders; give the result a free check too")
        cy = os.path.join(cdir, "case.yaml")
        if os.path.isfile(cy):
            import yaml
            with open(cy, encoding="utf-8") as f:
                doc = yaml.safe_load(f) or {}
            if doc.get("schema_version") != "1.1" or not doc.get("name"):
                problems.append(f"{c}/case.yaml: needs schema_version \"1.1\" and name")
            sc = (doc.get("context") or {}).get("scaffold_script")
            if sc and not os.path.isfile(os.path.join(cdir, sc)):
                problems.append(f"{c}/case.yaml: scaffold_script {sc} is missing")
            if sc and os.path.isfile(os.path.join(cdir, sc)):
                with open(os.path.join(cdir, sc), encoding="utf-8") as f:
                    if "@@" in f.read():
                        problems.append(f"{c}/{sc}: a placeholder was not filled")
    hooks = os.path.join(plugin, "hooks", "hooks.json")
    with open(hooks, encoding="utf-8") as f:
        events = json.load(f)["hooks"]
    for need in ("PreToolUse", "Stop", "SessionStart"):
        if need not in events:
            problems.append(f"hooks/hooks.json: no {need} hook (the repo layer's hooks were not carried over)")
    for name in ("pr", "chain-intent", "verify"):
        if not os.path.isfile(os.path.join(plugin, "skills", name, "SKILL.md")):
            problems.append(f"skills/{name}/SKILL.md is missing")
    return problems


def eval_command(plugin, out_dir, extra=()):
    cfg = load_config()
    cmd = ["claude", "plugin", "eval", plugin, "--scaffold", "--trust-plugin", "--no-publish",
           "--json", os.path.join(os.path.abspath(out_dir), "result.json"),
           "--model", cfg["model"], "--judge-model", cfg["judge_model"], "--threshold", str(cfg["threshold"]),
           "--runs", str(cfg["runs"]), "--max-cost-usd", str(cfg["max_cost_usd"]),
           "--allow-tools", *cfg["allow_tools"]]
    return cmd + list(extra)


def main(argv):
    ap = argparse.ArgumentParser(prog="plugin_eval.py", description=__doc__.split("\n")[0])
    ap.add_argument("cmd", choices=["build", "check", "run", "command"])
    ap.add_argument("--out")
    ap.add_argument("extra", nargs="*", help="after --: more options for claude plugin eval")
    a = ap.parse_args(argv)
    out = a.out or tempfile.mkdtemp(prefix="agentive-plugin-eval-")
    os.makedirs(out, exist_ok=True)
    plugin = build(out)
    if a.cmd == "build":
        print(plugin)
        return 0
    if a.cmd == "check":
        problems = check_suite(plugin)
        for p in problems:
            print("PLUGIN_EVAL problem:", p)
        print(f"PLUGIN_EVAL check: {'ok' if not problems else 'FAIL'} ({plugin})")
        return 1 if problems else 0
    cmd = eval_command(plugin, out, a.extra)
    if a.cmd == "command":
        print(" ".join(cmd))
        return 0
    problems = check_suite(plugin)
    if problems:
        print("\n".join("PLUGIN_EVAL problem: " + p for p in problems))
        return 1
    if not shutil.which("claude"):
        print("PLUGIN_EVAL: the claude CLI is not installed; NEXT=npm i -g @anthropic-ai/claude-code")
        return 2
    print("PLUGIN_EVAL: real model calls start now (" + " ".join(cmd[:4]) + " ...)", flush=True)
    return subprocess.run(cmd, cwd=plugin).returncode


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
