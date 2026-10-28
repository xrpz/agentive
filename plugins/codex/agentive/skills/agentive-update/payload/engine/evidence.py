# agentive-unit: eng.apply
# design: spec.init_apply.evidence_only, spec.init_apply.contract (evidence_precedence, evidence_scope, tie_break D6), spec.init_apply.empty_dir, spec.enforcement.secret_scan.wire_existing
"""Read the repo evidence and derive slot candidates. Deterministic, no network, never writes.

Evidence precedence (rank): 1 code or lockfile (package files and lockfiles, tool config named in them),
2 CI or build config (Makefile targets, linter config files), 3 README, 4 ecosystem official default.
Tier 4 applies only inside an ecosystem that ranks 1-3 already show (a package file or lockfile names it);
with no ecosystem evidence every stack verb is pending and nothing is guessed (init.empty_dir).
Several ecosystems: the primary one is the one with the most source files (D6 "most used in existing code");
only it supplies verbs, the others are named in the orientation [설계 판단]. Only root-level package files are
read in v1 [설계 판단]; the skills give anything ambiguous through --values (the LLM part of derive-<slot>).
"""
import json
import os
import re

__all__ = ["scan", "EVIDENCE_FILES", "VERB_ORDER"]

VERB_ORDER = ["setup", "build", "lint", "typecheck", "test", "verify", "run"]
TIERS = {"setup": "outer", "build": "outer", "lint": "inner", "typecheck": "inner", "test": "inner", "verify": "inner", "run": "outer"}
SKIP_DIRS = {".git", "node_modules", ".venv", "venv", "env", "__pycache__", "dist", "build", "target", "out", ".next",
             "vendor", "third_party", ".agents", ".claude", ".codex", ".github", ".idea", ".vscode", ".gradle", ".mvn",
             "coverage", ".tox", ".mypy_cache", ".pytest_cache", ".ruff_cache"}
EXT = {"node": (".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs"), "python": (".py",), "go": (".go",),
       "java": (".java", ".kt", ".kts"), "rust": (".rs",), "ruby": (".rb",), "php": (".php",), "dotnet": (".cs", ".fs")}
EVIDENCE_FILES = ["package.json", "package-lock.json", "pnpm-lock.yaml", "yarn.lock", "bun.lockb", "bun.lock",
                  "tsconfig.json", "pyproject.toml", "setup.py", "setup.cfg", "requirements.txt", "requirements-dev.txt",
                  "Pipfile", "Pipfile.lock", "poetry.lock", "uv.lock", "go.mod", "go.sum", "pom.xml", "build.gradle",
                  "build.gradle.kts", "settings.gradle", "settings.gradle.kts", "Cargo.toml", "Cargo.lock", "Gemfile",
                  "Gemfile.lock", "composer.json", "composer.lock", "Makefile"]
STACK_NAMES = ["TypeScript", "JavaScript", "Node.js", "Python", "Golang", "Go", "Java", "Kotlin", "Rust", "Ruby", "PHP",
               "C#", ".NET", "Swift", "Elixir", "Scala"]
STACK_CONTEXT = re.compile(r"(?i)(stack|language|written in|built with|runtime|framework|tech|스택|언어|기술)")


def _read(root, rel):
    try:
        with open(os.path.join(root, rel), encoding="utf-8", errors="replace") as f:
            return f.read()
    except OSError:
        return None


def _exists(root, rel):
    return os.path.exists(os.path.join(root, rel))


def _count_sources(root, limit=20000):
    counts, seen = {k: 0 for k in EXT}, 0
    for d, dirs, files in os.walk(root):
        dirs[:] = sorted(x for x in dirs if x not in SKIP_DIRS and not x.startswith("."))
        for name in files:
            seen += 1
            if seen > limit:
                return counts
            for eco, exts in EXT.items():
                if name.endswith(exts):
                    counts[eco] += 1
    return counts


def _verb(cmd, rank, source, files, fail_count=None, verb=None):
    v = {"cmd": cmd, "tier": TIERS.get(verb, "inner"), "evidence": {"rank": rank, "source": source, "files": sorted(set(files))}}
    if fail_count:
        v["fail_count"] = fail_count
    return v


# ------------------------------------------------------------------ ecosystems
def _node(root):
    text = _read(root, "package.json")
    if text is None:
        return None
    try:
        pkg = json.loads(text)
    except ValueError:
        pkg = {}
    scripts = pkg.get("scripts") if isinstance(pkg.get("scripts"), dict) else {}
    deps = {}
    for k in ("dependencies", "devDependencies"):
        if isinstance(pkg.get(k), dict):
            deps.update(pkg[k])
    lock = next((f for f in ("pnpm-lock.yaml", "yarn.lock", "bun.lockb", "bun.lock", "package-lock.json") if _exists(root, f)), None)
    pm = {"pnpm-lock.yaml": "pnpm", "yarn.lock": "yarn", "bun.lockb": "bun", "bun.lock": "bun"}.get(lock, "npm")
    pm_field = str(pkg.get("packageManager") or "")
    for name in ("pnpm", "yarn", "bun", "npm"):
        if not lock and pm_field.startswith(name + "@"):
            pm = name
    files = ["package.json"] + ([lock] if lock else [])

    def run(s):
        if s == "test":
            return {"npm": "npm test", "pnpm": "pnpm test", "yarn": "yarn test", "bun": "bun run test"}[pm]
        if s == "start":
            return {"npm": "npm start", "pnpm": "pnpm start", "yarn": "yarn start", "bun": "bun run start"}[pm]
        return f"{pm} run {s}"
    exe = {"npm": "npx", "pnpm": "pnpm exec", "yarn": "yarn", "bun": "bunx"}[pm]
    verbs = {}
    if lock:
        verbs["setup"] = _verb({"npm": "npm ci", "pnpm": "pnpm install --frozen-lockfile", "bun": "bun install --frozen-lockfile",
                                "yarn": "yarn install --immutable" if _exists(root, ".yarnrc.yml") else "yarn install --frozen-lockfile"}[pm],
                               1, lock, files, verb="setup")
    t = scripts.get("test")
    if isinstance(t, str) and t.strip() and "no test specified" not in t:
        fc = r"^\s+●" if ("jest" in t or "jest" in deps) else (r"^not ok " if "node --test" in t else None)
        verbs["test"] = _verb(run("test"), 1, "package.json scripts.test", files, fc, "test")
    if isinstance(scripts.get("lint"), str):
        verbs["lint"] = _verb(run("lint"), 1, "package.json scripts.lint", files, verb="lint")
    else:
        for cfg in ("eslint.config.js", "eslint.config.mjs", "eslint.config.cjs", "eslint.config.ts", ".eslintrc.js",
                    ".eslintrc.cjs", ".eslintrc.json", ".eslintrc.yml", ".eslintrc.yaml", ".eslintrc"):
            if _exists(root, cfg):
                verbs["lint"] = _verb(f"{exe} eslint .", 2, cfg, files + [cfg], verb="lint")
                break
        else:
            if _exists(root, "biome.json") or _exists(root, "biome.jsonc"):
                cfg = "biome.json" if _exists(root, "biome.json") else "biome.jsonc"
                verbs["lint"] = _verb(f"{exe} biome check .", 2, cfg, files + [cfg], verb="lint")
    for s in ("typecheck", "type-check", "check-types", "tsc", "types"):
        if isinstance(scripts.get(s), str):
            verbs["typecheck"] = _verb(run(s), 1, f"package.json scripts.{s}", files, verb="typecheck")
            break
    else:
        if _exists(root, "tsconfig.json") and "typescript" in deps:
            verbs["typecheck"] = _verb(f"{exe} tsc --noEmit", 4, "TypeScript compiler default (tsc --noEmit)", files + ["tsconfig.json"], verb="typecheck")
    if isinstance(scripts.get("build"), str):
        verbs["build"] = _verb(run("build"), 1, "package.json scripts.build", files, verb="build")
    if isinstance(scripts.get("start"), str):
        verbs["run"] = _verb(run("start"), 1, "package.json scripts.start", files, verb="run")
    elif isinstance(scripts.get("dev"), str):
        verbs["run"] = _verb(run("dev"), 1, "package.json scripts.dev", files, verb="run")
    gen = ["dist", "build", "out", ".next", "coverage"]
    return {"id": "node", "label": "TypeScript/JavaScript (Node.js, " + pm + ")" if _exists(root, "tsconfig.json") else "JavaScript (Node.js, " + pm + ")",
            "files": files, "verbs": verbs, "generated": gen, "vendor": []}


def _python(root):
    names = ["pyproject.toml", "setup.py", "setup.cfg", "requirements.txt", "Pipfile", "uv.lock", "poetry.lock", "Pipfile.lock"]
    present = [n for n in names if _exists(root, n)]
    if not present:
        return None
    py = _read(root, "pyproject.toml") or ""
    cfg = _read(root, "setup.cfg") or ""
    tox = _read(root, "tox.ini") or ""
    reqs = "\n".join(_read(root, n) or "" for n in ("requirements.txt", "requirements-dev.txt", "requirements_dev.txt", "Pipfile"))
    allt = py + "\n" + cfg + "\n" + reqs

    def has_dep(name):
        return re.search(r"(?im)(^|[\s\"'\[,])" + re.escape(name) + r"([\s\"'<>=~!\],;\[]|$)", allt) is not None
    if _exists(root, "uv.lock") or "[tool.uv" in py:
        pre, setup, lockf = "uv run ", "uv sync --frozen" if _exists(root, "uv.lock") else "uv sync", "uv.lock"
    elif _exists(root, "poetry.lock") or "[tool.poetry" in py:
        pre, setup, lockf = "poetry run ", "poetry install", "poetry.lock"
    elif _exists(root, "Pipfile.lock"):
        pre, setup, lockf = "pipenv run ", "pipenv sync --dev", "Pipfile.lock"
    else:
        pre, setup, lockf = "", "python3 -m pip install -r requirements.txt" if _exists(root, "requirements.txt") else None, "requirements.txt"
    files = present
    verbs = {}
    if setup:
        verbs["setup"] = _verb(setup, 1, lockf, files, verb="setup")

    def tool(name):
        return f"{pre}{name}" if pre else name
    pytest_cfg = "[tool.pytest" in py or "[tool:pytest]" in cfg or "[pytest]" in tox or _exists(root, "pytest.ini") or _exists(root, "conftest.py")
    if pytest_cfg or has_dep("pytest"):
        src = "pytest.ini" if _exists(root, "pytest.ini") else ("pyproject.toml" if "pytest" in py else present[0])
        verbs["test"] = _verb(f"{pre}pytest" if pre else "python3 -m pytest", 1, f"{src} (pytest)", files, r"^FAILED ", "test")
    else:
        tdir = next((d for d in ("tests", "test") if os.path.isdir(os.path.join(root, d))), None)
        if tdir and any(n.startswith("test") and n.endswith(".py") for n in os.listdir(os.path.join(root, tdir))):
            verbs["test"] = _verb(f"{pre}python -m unittest discover -s {tdir}" if pre else f"python3 -m unittest discover -s {tdir}",
                                  4, "unittest (Python standard library default)", files, r"^(FAIL|ERROR): ", "test")
    if "[tool.ruff" in py or _exists(root, "ruff.toml") or _exists(root, ".ruff.toml") or has_dep("ruff"):
        verbs["lint"] = _verb(tool("ruff check ."), 1, "ruff configuration", files + [f for f in ("ruff.toml", ".ruff.toml") if _exists(root, f)], verb="lint")
    elif _exists(root, ".flake8") or "[flake8]" in cfg or "[flake8]" in tox or has_dep("flake8"):
        verbs["lint"] = _verb(tool("flake8"), 2, "flake8 configuration", files + [f for f in (".flake8",) if _exists(root, f)], verb="lint")
    if "[tool.mypy" in py or _exists(root, "mypy.ini") or _exists(root, ".mypy.ini") or "[mypy]" in cfg or has_dep("mypy"):
        verbs["typecheck"] = _verb(tool("mypy ."), 1, "mypy configuration", files + [f for f in ("mypy.ini", ".mypy.ini") if _exists(root, f)], verb="typecheck")
    elif "[tool.pyright" in py or _exists(root, "pyrightconfig.json") or has_dep("pyright"):
        verbs["typecheck"] = _verb(tool("pyright"), 1, "pyright configuration", files, verb="typecheck")
    if "[build-system]" in py:
        if pre == "uv run ":
            verbs["build"] = _verb("uv build", 1, "pyproject.toml [build-system]", files, verb="build")
        elif pre == "poetry run ":
            verbs["build"] = _verb("poetry build", 1, "pyproject.toml [build-system]", files, verb="build")
    return {"id": "python", "label": "Python" + (f" ({pre.split()[0]})" if pre else ""), "files": files, "verbs": verbs,
            "generated": ["build", "dist", "htmlcov"], "vendor": []}


def _go(root):
    if not _exists(root, "go.mod"):
        return None
    files = ["go.mod"] + (["go.sum"] if _exists(root, "go.sum") else [])
    verbs = {"setup": _verb("go mod download", 4, "go toolchain default", files, verb="setup"),
             "build": _verb("go build ./...", 4, "go toolchain default", files, verb="build"),
             "test": _verb("go test ./...", 4, "go toolchain default", files, r"^--- FAIL", "test")}
    cfg = next((f for f in (".golangci.yml", ".golangci.yaml", ".golangci.toml", ".golangci.json") if _exists(root, f)), None)
    verbs["lint"] = _verb("golangci-lint run", 2, cfg, files + [cfg], verb="lint") if cfg else _verb("go vet ./...", 4, "go toolchain default (go vet)", files, verb="lint")
    if any(n.endswith(".go") and "package main" in (_read(root, n) or "") for n in os.listdir(root)):
        verbs["run"] = _verb("go run .", 4, "go toolchain default", files, verb="run")
    return {"id": "go", "label": "Go", "files": files, "verbs": verbs, "generated": ["bin"], "vendor": ["vendor"]}


def _java(root):
    if _exists(root, "pom.xml"):
        pom = _read(root, "pom.xml") or ""
        mvn = "./mvnw" if _exists(root, "mvnw") else "mvn"
        files = ["pom.xml"]
        verbs = {"setup": _verb(f"{mvn} -B -q dependency:go-offline", 4, "maven default", files, verb="setup"),
                 "typecheck": _verb(f"{mvn} -B -q compile", 4, "maven lifecycle (compile)", files, verb="typecheck"),
                 "build": _verb(f"{mvn} -B -q package -DskipTests", 4, "maven lifecycle (package)", files, verb="build"),
                 "test": _verb(f"{mvn} -B test", 4, "maven lifecycle (test)", files, r"^\[ERROR\]   \S", "test")}
        for plug, goal in (("maven-checkstyle-plugin", "checkstyle:check"), ("spotless-maven-plugin", "spotless:check"), ("maven-pmd-plugin", "pmd:check")):
            if plug in pom:
                verbs["lint"] = _verb(f"{mvn} -B -q {goal}", 1, f"pom.xml {plug}", files, verb="lint")
                break
        return {"id": "java", "label": "Java (Maven)", "files": files, "verbs": verbs, "generated": ["target"], "vendor": []}
    gfile = next((f for f in ("build.gradle.kts", "build.gradle") if _exists(root, f)), None)
    if gfile:
        g = "./gradlew" if _exists(root, "gradlew") else "gradle"
        text = _read(root, gfile) or ""
        files = [gfile] + [f for f in ("settings.gradle", "settings.gradle.kts") if _exists(root, f)]
        verbs = {"typecheck": _verb(f"{g} classes", 4, "gradle java plugin (classes)", files, verb="typecheck"),
                 "build": _verb(f"{g} assemble", 4, "gradle default (assemble)", files, verb="build"),
                 "test": _verb(f"{g} test", 4, "gradle default (test)", files, r"FAILED$", "test")}
        if "spotless" in text:
            verbs["lint"] = _verb(f"{g} spotlessCheck", 1, f"{gfile} spotless", files, verb="lint")
        elif "checkstyle" in text:
            verbs["lint"] = _verb(f"{g} checkstyleMain", 1, f"{gfile} checkstyle", files, verb="lint")
        return {"id": "java", "label": "Java/Kotlin (Gradle)", "files": files, "verbs": verbs, "generated": ["build"], "vendor": []}
    return None


def _rust(root):
    if not _exists(root, "Cargo.toml"):
        return None
    files = ["Cargo.toml"] + (["Cargo.lock"] if _exists(root, "Cargo.lock") else [])
    verbs = {"setup": _verb("cargo fetch", 4, "cargo default", files, verb="setup"),
             "build": _verb("cargo build", 4, "cargo default", files, verb="build"),
             "lint": _verb("cargo clippy -- -D warnings", 4, "cargo clippy (rustup component)", files, verb="lint"),
             "typecheck": _verb("cargo check", 4, "cargo default", files, verb="typecheck"),
             "test": _verb("cargo test", 4, "cargo default", files, r"^test .* FAILED$", "test")}
    if _exists(root, "src/main.rs"):
        verbs["run"] = _verb("cargo run", 4, "cargo default", files, verb="run")
    return {"id": "rust", "label": "Rust", "files": files, "verbs": verbs, "generated": ["target"], "vendor": []}


def _other(root, eid, label, names):
    present = [n for n in names if _exists(root, n)]
    if not present:
        return None
    return {"id": eid, "label": label, "files": present, "verbs": {}, "generated": [], "vendor": ["vendor"] if eid in ("php", "ruby") else []}


def _make_targets(root):
    text = _read(root, "Makefile")
    if not text:
        return {}
    targets = set(re.findall(r"(?m)^([A-Za-z][A-Za-z0-9_-]*)\s*:(?!=)", text))
    out = {}
    for verb, cands in (("test", ["test"]), ("lint", ["lint"]), ("typecheck", ["typecheck", "type-check", "check-types"]),
                        ("build", ["build"]), ("setup", ["setup", "install", "deps"]), ("run", ["run", "start"])):
        for c in cands:
            if c in targets:
                out[verb] = _verb(f"make {c}", 2, f"Makefile target {c}", ["Makefile"], verb=verb)
                break
    return out


def _secret_scanner(root):
    pc = _read(root, ".pre-commit-config.yaml") or ""
    if _exists(root, ".gitleaks.toml") or re.search(r"(?m)id:\s*gitleaks\b", pc):
        src = ".gitleaks.toml" if _exists(root, ".gitleaks.toml") else ".pre-commit-config.yaml"
        return _verb("gitleaks protect --staged --redact", 1, f"{src} (gitleaks staged scan)", [src], verb="verify")
    if _exists(root, ".secrets.baseline"):
        return _verb("git diff --cached --name-only -z | xargs -0 detect-secrets-hook --baseline .secrets.baseline", 1,
                     ".secrets.baseline (detect-secrets staged scan)", [".secrets.baseline"], verb="verify")
    if re.search(r"(?m)id:\s*trufflehog\b", pc):
        return _verb("trufflehog git file://. --since-commit HEAD --results=verified,unknown --fail", 1,
                     ".pre-commit-config.yaml (trufflehog diff scan)", [".pre-commit-config.yaml"], verb="verify")
    return None


def _stated_stack(root):
    cands = sorted(n for n in os.listdir(root) if n.lower().startswith("readme")) if os.path.isdir(root) else []
    for d in ("docs", os.path.join("docs", "plans")):
        p = os.path.join(root, d)
        if os.path.isdir(p):
            for dd, dirs, files in os.walk(p):
                dirs.sort()
                for f in sorted(files):
                    if f.endswith(".md"):
                        cands.append(os.path.relpath(os.path.join(dd, f), root).replace(os.sep, "/"))
    for rel in cands:
        text = _read(root, rel) or ""
        for line in text.splitlines():
            if not STACK_CONTEXT.search(line):
                continue
            for name in STACK_NAMES:
                if re.search(r"(?<![A-Za-z0-9])" + re.escape(name) + r"(?![A-Za-z0-9])", line):
                    if name == "Go" and re.search(r"\bGo\s+(to|ahead|back)\b", line):
                        continue
                    return {"stack": name, "source": rel}
    return None


def _git_ignored(root, rel):
    import subprocess
    try:
        r = subprocess.run(["git", "-C", root, "check-ignore", "-q", rel + "/"], capture_output=True, timeout=10)
    except (OSError, subprocess.SubprocessError):
        return False
    return r.returncode == 0


def _paths(root, eco):
    def entries(names):
        out = []
        for n in names:
            if os.path.isdir(os.path.join(root, n)):
                out.append({"path": n, "proof": "exists"})
            elif eco is not None and _git_ignored(root, n):
                out.append({"path": n, "proof": "gitignored"})
        return out
    src = [d for d in ("src", "lib", "app", "pkg", "cmd", "internal") if os.path.isdir(os.path.join(root, d))]
    if eco and eco["id"] == "python" and not src:
        for n in sorted(os.listdir(root)):
            if n not in SKIP_DIRS and not n.startswith(".") and n not in ("tests", "test") and os.path.isfile(os.path.join(root, n, "__init__.py")):
                src.append(n)
    tests = [d for d in ("tests", "test", "__tests__", "spec", "src/test") if os.path.isdir(os.path.join(root, d))]
    gen = entries(eco["generated"]) if eco else []
    vendor = [{"path": n, "proof": "exists"} for n in ((eco["vendor"] if eco else []) + ["third_party"]) if os.path.isdir(os.path.join(root, n))]
    return {"src": [{"path": p, "proof": "exists"} for p in src], "tests": [{"path": p, "proof": "exists"} for p in tests],
            "generated": gen, "vendor": vendor}


def scan(root):
    """Evidence summary: {ecosystems, primary, labels, files, verbs, paths, stated, secret_scanner}."""
    found = [e for e in (_node(root), _python(root), _go(root), _java(root), _rust(root),
                         _other(root, "ruby", "Ruby", ["Gemfile", "Gemfile.lock"]),
                         _other(root, "php", "PHP", ["composer.json", "composer.lock"]),
                         _other(root, "dotnet", ".NET", [n for n in (os.listdir(root) if os.path.isdir(root) else []) if n.endswith((".csproj", ".sln", ".fsproj"))]))
             if e]
    counts = _count_sources(root) if found else {}
    order = {"node": 0, "python": 1, "go": 2, "java": 3, "rust": 4, "ruby": 5, "php": 6, "dotnet": 7}
    found.sort(key=lambda e: (-counts.get(e["id"], 0), order.get(e["id"], 99)))
    primary = found[0] if found else None
    verbs = dict(primary["verbs"]) if primary else {}
    if primary:
        for v, spec in _make_targets(root).items():
            if v not in verbs or verbs[v]["evidence"]["rank"] > 2:
                verbs[v] = spec
    secret = _secret_scanner(root) if primary else None
    inner = [v for v in ("lint", "typecheck", "test", "build") if v in verbs and verbs[v]["tier"] == "inner"]
    if secret:
        verbs["verify"] = secret
    elif inner:
        verbs["verify"] = _verb(".agents/bin/verb verify", 4, "framework", [".agents/manifest.yaml"], verb="verify")
    return {"ecosystems": [e["id"] for e in found], "primary": primary["id"] if primary else None,
            "labels": [e["label"] for e in found], "files": sorted({f for e in found for f in e["files"]}),
            "verbs": verbs, "paths": _paths(root, primary), "stated": None if found else _stated_stack(root),
            "secret_scanner": bool(secret)}
