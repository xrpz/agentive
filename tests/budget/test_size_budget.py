# agentive-unit: selftest.size_budget
# design: decisions.accepted[SIZEBUDGET], spec.repo_layer.files[f-agents-md], spec.framework_repo.self_tests
"""Size limits of the text that agents load, taken from the vendors' own pages and checked on the installed result.

  installed AGENTS.md as rendered for each sample repo, after @-import expansion: at most 200 lines (Claude Code memory page:
      "target under 200 lines per CLAUDE.md file") and at most 32 KiB (Codex AGENTS.md page: project_doc_max_bytes, 32 KiB by default)
  skill descriptions: description + when_to_use at most 1,536 characters per skill (Claude Code skills page) and the descriptions of
      the skills one tool lists at most 8,000 characters together (Codex skills page: "or 8,000 characters when the context window is
      unknown"; Claude Code's budget is 1% of the context window and gives no character count, so it is not checked)
  this repo's AGENTS.md: at most 200 lines (same Claude Code page)
Not checked, because no official page gives the number: tokens of a skill, a total over all always-loaded text.
Each test prints its numbers to stderr so the self-test excerpt shows how much room is left."""
import concurrent.futures
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest

import yaml  # dev-time only

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
PAYLOAD = os.path.join(ROOT, "plugins", "claude-code", "agentive", "payload")
ENGINE = os.path.join(PAYLOAD, "engine", "apply.py")
STACKS = ["ts", "py", "go", "java"]
MAX_LINES = 200
MAX_BYTES = 32 * 1024
MAX_SKILL_TEXT = 1536
MAX_SKILL_LIST = 8000
IMPORT = re.compile(r"(?<![\w`])@((?:\.{0,2}/)?[\w][\w./-]*\.\w+)")
FENCE = re.compile(r"^\s*(```|~~~)")
CODE_SPAN = re.compile(r"`[^`\n]*`")


def say(msg):
    sys.stderr.write("SIZE " + msg + "\n")


def expand_imports(path, depth=0, seen=None):
    """The text Claude Code loads for a file: every `@path` outside code spans and fenced blocks is replaced by the file it names
    (relative to the importing file, at most four hops, a missing file stays as written), as the memory page describes."""
    seen = seen or set()
    real = os.path.realpath(path)
    with open(path, encoding="utf-8") as f:
        lines = f.read().split("\n")
    if lines and lines[-1] == "":
        lines.pop()
    out, fenced = [], False
    for line in lines:
        if FENCE.match(line):
            fenced = not fenced
            out.append(line)
            continue
        if fenced or depth >= 4:
            out.append(line)
            continue
        out.append(line)
        for m in IMPORT.finditer(CODE_SPAN.sub("", line)):
            target = os.path.normpath(os.path.join(os.path.dirname(path), m.group(1)))
            if os.path.isfile(target) and os.path.realpath(target) not in seen | {real}:
                out.extend(expand_imports(target, depth + 1, seen | {real}).split("\n"))
    return "\n".join(out)


def unescape(rel):
    return "/".join(("." + c[4:]) if c.startswith("dot-") else c for c in rel.split("/"))


def install_fixture(stack, dest):
    src = os.path.join(ROOT, "fixtures", stack)
    for base, dirs, files in os.walk(src):
        dirs[:] = [d for d in dirs if d not in ("node_modules", "__pycache__", ".gradle", "build", "dist")]
        for f in files:
            rel = os.path.relpath(os.path.join(base, f), src).replace(os.sep, "/")
            out = os.path.join(dest, unescape(rel))
            os.makedirs(os.path.dirname(out), exist_ok=True)
            shutil.copy2(os.path.join(base, f), out)
    env = dict(os.environ, GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@example.com", GIT_COMMITTER_NAME="t",
               GIT_COMMITTER_EMAIL="t@example.com", GIT_CONFIG_NOSYSTEM="1", HOME=dest)
    for args in (["init", "-q", "-b", "main"], ["add", "-A"], ["commit", "-q", "-m", "fixture"]):
        r = subprocess.run(["git", "-C", dest] + args, capture_output=True, text=True, env=env)
        if r.returncode != 0:
            raise AssertionError(f"git {args}: {r.stderr}")
    env = {k: v for k, v in os.environ.items() if k not in ("CI", "GITHUB_ACTIONS", "AGENTIVE_HEADLESS")}
    r = subprocess.run([sys.executable, ENGINE, "init", "--repo", dest, "--no-managed-review-copy"], capture_output=True, text=True, env=env)
    if r.returncode != 0:
        raise AssertionError("init failed:\n" + (r.stdout + r.stderr)[-2000:])


def frontmatter(path):
    with open(path, encoding="utf-8") as f:
        text = f.read()
    m = re.match(r"---\n(.*?)\n---\n", text, re.S)
    return yaml.safe_load(m.group(1)) if m else {}


def skill_text(path):
    fm = frontmatter(path)
    return str(fm.get("description") or ""), str(fm.get("when_to_use") or "")


def skill_files(*parts):
    base = os.path.join(ROOT, *parts)
    return sorted(os.path.join(base, d, "SKILL.md") for d in os.listdir(base) if os.path.isfile(os.path.join(base, d, "SKILL.md")))


@unittest.skipUnless(os.path.isfile(ENGINE), "run python3 tools/generate.py first: the payload engine is missing")
class InstalledAgentsMd(unittest.TestCase):
    def test_expanded_agents_md_fits_the_limits_on_every_sample_repo(self):
        worst = (0, 0)
        dirs = {}
        for stack in STACKS:
            dirs[stack] = tempfile.mkdtemp(prefix=f"agentive-size-{stack}-")
            self.addCleanup(shutil.rmtree, dirs[stack], True)
        # the four installs are independent processes in their own directories: run them side by side [설계 판단]
        with concurrent.futures.ThreadPoolExecutor(max_workers=min(len(STACKS), os.cpu_count() or 1)) as pool:
            list(pool.map(lambda stack: install_fixture(stack, dirs[stack]), STACKS))   # an AssertionError from an install is re-raised here
        for stack in STACKS:
            d = dirs[stack]
            text = expand_imports(os.path.join(d, "AGENTS.md"))
            lines, size = len(text.split("\n")), len(text.encode("utf-8"))
            loaded = expand_imports(os.path.join(d, "CLAUDE.md"))
            say(f"installed AGENTS.md on fixtures/{stack}: {lines} lines, {size} bytes after import expansion; "
                f"CLAUDE.md as Claude Code loads it: {len(loaded.split(chr(10)))} lines (limits {MAX_LINES} lines, {MAX_BYTES} bytes)")
            self.assertLessEqual(lines, MAX_LINES, f"{stack}: installed AGENTS.md is {lines} lines after @-import expansion")
            self.assertLessEqual(size, MAX_BYTES, f"{stack}: installed AGENTS.md is {size} bytes after @-import expansion")
            worst = max(worst, (lines, size))
        self.assertGreater(worst[0], 20, "the measured file is the rendered AGENTS.md, not an empty one")

    def test_import_expansion_counts_an_imported_file(self):
        """The measurement follows `@path` (so an import cannot hide lines) and skips code spans and fenced blocks."""
        d = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, d, True)
        with open(os.path.join(d, "big.md"), "w", encoding="utf-8") as f:
            f.write("\n".join(f"line {i}" for i in range(60)) + "\n")
        with open(os.path.join(d, "AGENTS.md"), "w", encoding="utf-8") as f:
            f.write("intro\nRules: @big.md\nnot an import: `@big.md`\n```\n@big.md\n```\n")
        text = expand_imports(os.path.join(d, "AGENTS.md"))
        self.assertEqual(text.count("line 59"), 1)
        self.assertEqual(len(text.split("\n")), 6 + 60)


class SkillDescriptions(unittest.TestCase):
    def test_each_skill_text_fits_the_claude_listing_cut(self):
        too_long = []
        for path in skill_files("src", "repo-layer", "dot-agents", "skills") + skill_files("src", "plugin", "claude-code", "skills"):
            d, w = skill_text(path)
            if len(d) + len(w) > MAX_SKILL_TEXT:
                too_long.append((os.path.relpath(path, ROOT), len(d) + len(w)))
        self.assertEqual(too_long, [], f"description + when_to_use is cut at {MAX_SKILL_TEXT} characters in the skill listing")

    def test_skills_one_tool_lists_fit_the_codex_list_budget(self):
        repo = [sum(map(len, skill_text(p))) for p in skill_files("src", "repo-layer", "dot-agents", "skills")]
        for tool, sub in (("claude", ("src", "plugin", "claude-code", "skills")), ("codex", ("src", "plugin", "codex", "skills"))):
            plug = [sum(map(len, skill_text(p))) for p in skill_files(*sub)]
            total = sum(repo) + sum(plug)
            say(f"skill descriptions listed with the {tool} plugin: {total} characters in {len(repo) + len(plug)} skills "
                f"(repo skills {sum(repo)}, plugin skills {sum(plug)}; limit {MAX_SKILL_LIST})")
            self.assertLessEqual(total, MAX_SKILL_LIST, f"{tool}: the skill descriptions together exceed {MAX_SKILL_LIST} characters")
        self.assertGreaterEqual(len(repo), 9)


class FrameworkAgentsMd(unittest.TestCase):
    def test_this_repos_agents_md_is_under_200_lines(self):
        with open(os.path.join(ROOT, "AGENTS.md"), encoding="utf-8") as f:
            n = len(f.read().splitlines())
        say(f"framework AGENTS.md: {n} lines (limit {MAX_LINES})")
        self.assertLessEqual(n, MAX_LINES)


if __name__ == "__main__":
    unittest.main()
