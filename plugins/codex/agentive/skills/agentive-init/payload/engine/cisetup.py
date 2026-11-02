# agentive-unit: eng.apply
# design: spec.work_loop.ci_ai_review_floor.setup_steps, decisions.accepted[CISETUP]
"""render.ci_setup_steps: the toolchain steps of the outer-verbs job of .github/workflows/agentive-review.yml.

One group of steps per ecosystem of the evidence scan (the agentive manifest ecosystems), placed between checkout and the
verbs step, so `setup` (`npm ci`, `uv sync --frozen`) and the verbs find their tools. ubuntu-latest does not carry them all:
uv, pnpm and bun are not on the runner images, and the preinstalled Node, Python and Java versions change when ubuntu-latest
moves to Ubuntu 26.04 in 2026-11 (runner-images notice, 2026-10).

Versions come from the repo's own version files through the `*-version-file` inputs of the setup actions [design judgment: the
generators we found pin fixed versions; reading the file keeps CI on the version the repo already declares]:
  node    .nvmrc, .node-version, package.json engines.node, .tool-versions (nodejs)
  python  .python-version, pyproject.toml requires-python, .tool-versions (python)
  go      go.mod, go.work, .go-version
  java    .java-version, .sdkmanrc, .tool-versions (java); else the release/toolchain of build.gradle(.kts) or pom.xml
No version found: the step is still rendered (the runner's tool is used) with a comment line that says what to set.
Every step has a stable `id:` (toolchain-<name>), so a file under .agents/custom/ can insert a step before or after it
(`before:` / `after:` in an override list item, render.py). No ecosystem evidence: a commented placeholder step.
Action majors are the latest ones whose action.yml has the inputs used here (checked 2026-10-29).
"""
import json
import os
import re

NODE, PNPM, PYTHON, UV, GO, JAVA, BUN = ("actions/setup-node@v7", "pnpm/action-setup@v6", "actions/setup-python@v7",
                                          "astral-sh/setup-uv@v7", "actions/setup-go@v7", "actions/setup-java@v6", "oven-sh/setup-bun@v2")
FROM_RUNNER = ("rust", "ruby", "php", "dotnet")
DEFAULT_JAVA = "21"


def _read(root, rel):
    try:
        with open(os.path.join(root, rel), encoding="utf-8", errors="replace") as f:
            return f.read()
    except OSError:
        return None


def _tool_versions(root, name):
    t = _read(root, ".tool-versions")
    return bool(t) and any(ln.split()[:1] == [name] for ln in t.splitlines() if ln.strip() and not ln.startswith("#"))


def _step(sid, uses=None, with_=None, run=None, comments=()):
    out = [f"# {c}" for c in comments]
    out.append(f"- id: toolchain-{sid}")
    out.append(f"  uses: {uses}" if uses else f"  run: {run}")
    if with_:
        out.append("  with:")
        out += [f"    {k}: {v}" for k, v in with_.items()]
    return out


def node_steps(root):
    out = []
    pkg = _read(root, "package.json") or ""
    try:
        engines = (json.loads(pkg) if pkg else {}).get("engines") or {}
    except ValueError:
        engines = {}
    pm = next((n for f, n in (("pnpm-lock.yaml", "pnpm"), ("yarn.lock", "yarn"), ("bun.lock", "bun"), ("bun.lockb", "bun")) if os.path.isfile(os.path.join(root, f))), "npm")
    if pm == "pnpm":
        try:
            declared = str((json.loads(pkg) if pkg else {}).get("packageManager") or "").startswith("pnpm@")
        except ValueError:
            declared = False
        out += _step("pnpm", PNPM, {} if declared else {"version": "10"},
                     comments=() if declared else ("package.json has no packageManager pnpm@<version>: set the pnpm major of pnpm-lock.yaml",))
    if pm == "bun":
        out += _step("bun", BUN)
    vf = next((f for f in (".nvmrc", ".node-version") if os.path.isfile(os.path.join(root, f))), None)
    if not vf and engines.get("node"):
        vf = "package.json"
    if not vf and _tool_versions(root, "nodejs"):
        vf = ".tool-versions"
    out += _step("node", NODE, {"node-version-file": vf} if vf else {}, comments=() if vf else
                 ("no .nvmrc, .node-version or package.json engines.node: the runner's Node is used; set node-version: \"<major>\"",))
    if pm == "yarn":
        out += _step("corepack", run="corepack enable")
    return out


def python_steps(root):
    out = []
    pyproject = _read(root, "pyproject.toml") or ""
    vf = ".python-version" if os.path.isfile(os.path.join(root, ".python-version")) else None
    if not vf and re.search(r"^\s*requires-python\s*=", pyproject, re.M):
        vf = "pyproject.toml"
    if not vf and _tool_versions(root, "python"):
        vf = ".tool-versions"
    out += _step("python", PYTHON, {"python-version-file": vf} if vf else {}, comments=() if vf else
                 ("no .python-version or requires-python: the runner's Python is used; set python-version: \"<x.y>\"",))
    if os.path.isfile(os.path.join(root, "uv.lock")) or "[tool.uv" in pyproject:
        out += _step("uv", UV)
    elif os.path.isfile(os.path.join(root, "poetry.lock")) or "[tool.poetry" in pyproject:
        out += _step("poetry", run="python3 -m pip install poetry")
    elif os.path.isfile(os.path.join(root, "Pipfile.lock")):
        out += _step("pipenv", run="python3 -m pip install pipenv")
    return out


def go_steps(root):
    vf = next((f for f in ("go.mod", "go.work", ".go-version") if os.path.isfile(os.path.join(root, f))), None)
    return _step("go", GO, {"go-version-file": vf} if vf else {}, comments=() if vf else ("no go.mod: set go-version: \"<x.y>\"",))


def _java_release(root):
    """The Java major from a Gradle or Maven build file, or None."""
    def major(v):
        parts = str(v).replace("_", ".").split(".")
        return parts[1] if parts[0] == "1" and len(parts) > 1 else parts[0]
    for f in ("build.gradle.kts", "build.gradle"):
        t = _read(root, f)
        if t:
            m = re.search(r"JavaLanguageVersion\.of\(\s*(\d+)\s*\)|jvmToolchain\(\s*(\d+)\s*\)|"
                          r"(?:sourceCompatibility|targetCompatibility)\s*=\s*(?:JavaVersion\.VERSION_)?['\"]?([\d._]+)", t)
            if m:
                return major(next(g for g in m.groups() if g))
    t = _read(root, "pom.xml")
    if t:
        m = re.search(r"<(?:maven\.compiler\.release|release|java\.version|maven\.compiler\.source)>\s*([\d.]+)\s*<", t)
        if m:
            return major(m.group(1))
    return None


def java_steps(root):
    vf = next((f for f in (".java-version", ".sdkmanrc") if os.path.isfile(os.path.join(root, f))), None)
    if not vf and _tool_versions(root, "java"):
        vf = ".tool-versions"
    if vf:
        w = {"distribution": "temurin", "java-version-file": vf}
        return _step("java", JAVA, w)
    rel = _java_release(root)
    if rel:
        return _step("java", JAVA, {"distribution": "temurin", "java-version": f"\"{rel}\""},
                     comments=("version read from the build file (Gradle toolchain or Maven release); a .java-version file would take over",))
    return _step("java", JAVA, {"distribution": "temurin", "java-version": f"\"{DEFAULT_JAVA}\""},
                 comments=(f"no .java-version, .sdkmanrc or build-file release found: set the version your build needs (default {DEFAULT_JAVA})",))


def render(root, ecosystems):
    """The block (lines at column 0; the engine indents them to the placeholder) for the ecosystems of the evidence scan."""
    eco = list(ecosystems or [])
    if not eco:
        return "\n".join([
            "# No stack evidence yet, so no toolchain step. Run the agentive update command once the project has a package file:",
            "# it renders the setup steps of the stack. To add steps by hand put them in .agents/custom/.github/workflows/agentive-review.yml",
            "# (list item with an id and `before: verbs`). Placeholder:",
            "# - id: toolchain",
            "#   uses: actions/setup-node@v7          # or actions/setup-python, actions/setup-go, actions/setup-java, astral-sh/setup-uv"])
    lines = ["# toolchain steps from the agentive manifest ecosystems (" + ", ".join(eco) + "); versions from the repo's version files.",
             "# Insert your own step before or after one by id from .agents/custom/ (a list item with its own id and `before: <id>` or `after: <id>`)."]
    for name, fn in (("node", node_steps), ("python", python_steps), ("go", go_steps), ("java", java_steps)):
        if name in eco:
            lines += fn(root)
    left = [e for e in eco if e in FROM_RUNNER]
    if left:
        lines.append(f"# {', '.join(left)}: the tools come from the runner image (ubuntu-latest moves to Ubuntu 26.04 in 2026-11); add a setup step with a version if the build needs one.")
    return "\n".join(lines)
