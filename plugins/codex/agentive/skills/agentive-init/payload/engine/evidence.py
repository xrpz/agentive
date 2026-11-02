# agentive-unit: eng.apply
# design: spec.init_apply.evidence_only, spec.init_apply.installed_layer_excluded, spec.init_apply.contract (evidence_precedence, evidence_scope, tie_break D6), spec.init_apply.empty_dir, spec.enforcement.secret_scan.wire_existing
"""Read the repo evidence and derive slot candidates. Deterministic, no network, never writes.

Evidence precedence (rank): 1 code or lockfile (package files and lockfiles, tool config named in them),
2 CI or build config (Makefile targets, linter config files), 3 README, 4 ecosystem official default.
Tier 4 applies only inside an ecosystem that ranks 1-3 already show (a package file or lockfile names it);
with no ecosystem evidence every stack verb is pending and nothing is guessed (init.empty_dir).
Several ecosystems: the primary one is the one with the most source files (D6 "most used in existing code");
only it supplies verbs, the others are named in the orientation [design judgment]. Only root-level package files are
read in v1 [design judgment]; the skills give anything ambiguous through --values (the LLM part of derive-<slot>).
"""
import json
import os
import re
import shlex

__all__ = ["scan", "EVIDENCE_FILES", "VERB_ORDER", "lint_scope", "file_args_for", "OWNED_DIRS", "owned_exclude"]

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
# the three \u alternatives are the Korean words for stack, language, technology: READMEs written in Korean still count as stack context
# V1-14: also a build or implementation verb next to the name ("We will build this in TypeScript", "implemented in Go")
STACK_CONTEXT = re.compile(r"(?i)(stack|language|written in|built with|runtime|framework|tech|"
                           r"\b(build|built|building|implement(ed|ing)?|develop(ed|ing)?|writ(e|ten|ing)|us(e|es|ed|ing)|powered by|based on)\b|"
                           r"\uc2a4\ud0dd|\uc5b8\uc5b4|\uae30\uc220)")


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


# ------------------------------------------------------------------ the installed layer stays out of the project's tools
# E2E-01 [design judgment]: a derived command that walks the whole repository (`ruff check .`, `flake8`, `eslint .`) would
# also lint the agentive layer that init installs (.agents/lib/**/*.py, .agents/hooks, .agents/bin, .claude, .codex), so
# `.agents/bin/verb verify` was red right after install. Each derived repo-wide command therefore keeps those three
# directories out, by the tool's documented additive exclude option where it has one (it adds to the project's own
# exclude list instead of replacing it), else by naming the agentive manifest's src/tests paths:
#   ruff check   --extend-exclude .agents,.claude,.codex   (ruff settings: extend-exclude "in addition to" exclude;
#                                                          single-name patterns match the directory anywhere in the tree)
#   flake8       --extend-exclude .agents,.claude,.codex   (flake8 options, added in 3.8.0: comma-separated globs)
#   eslint       --ignore-pattern .agents/ ... (repeated)  (ESLint CLI: repeatable, relative to the working directory)
#   biome check  <src> <tests> from the agentive manifest  (Biome documents no CLI exclude option; `.` when none is known)
# Left as derived, because the tool already skips dot-named directories by its documented default: mypy (never
# recursively discovers names that start with a period), pyright (default exclude `**/.*`; read on a mirror of the pyright
# docs and measured with pyright 1.1.414, official page not reached: unverified), go vet / go build / go test ./... and
# golangci-lint (the go tool ignores directory names that begin with "." or "_"), tsc (the layer has no .ts files), and
# cargo, maven and gradle (they read their own project model, not the directory tree).
OWNED_DIRS = (".agents", ".claude", ".codex")


def owned_exclude(tool):
    """The flag text that keeps `tool`'s repo-wide run off OWNED_DIRS, or None when the tool has no additive one."""
    if tool in ("ruff", "flake8"):
        return "--extend-exclude " + ",".join(OWNED_DIRS)
    if tool == "eslint":
        return " ".join(f"--ignore-pattern {d}/" for d in OWNED_DIRS)
    return None


# E2E-21 (LOCKFILEDEPS): a lockfile is dependency data, never evidence of a lint, typecheck or format config (the review gate
# protects those evidence files as check config); same names as review/rv1.py LOCKFILES
LOCKFILES = {"package-lock.json", "npm-shrinkwrap.json", "yarn.lock", "pnpm-lock.yaml", "bun.lockb", "bun.lock",
             "poetry.lock", "Pipfile.lock", "uv.lock", "pdm.lock", "go.sum", "Cargo.lock", "Gemfile.lock",
             "composer.lock", "gradle.lockfile", "packages.lock.json", "mix.lock", "pubspec.lock", "flake.lock"}
CHECK_VERBS = ("lint", "typecheck", "format")


def _verb(cmd, rank, source, files, fail_count=None, verb=None):
    if verb in CHECK_VERBS:
        files = [f for f in files if os.path.basename(f) not in LOCKFILES]
    v = {"cmd": cmd, "tier": TIERS.get(verb, "inner"), "evidence": {"rank": rank, "source": source, "files": sorted(set(files))}}
    if fail_count:
        v["fail_count"] = fail_count
    return v


# ------------------------------------------------------------------ lint scope (verbs.lint.exts, verbs.lint.file_args)
# [design judgment] The PostToolUse lint hook runs the lint command on the edited files (`.agents/bin/verb lint -- <file>`;
# the wrapper replaces a trailing whole-repo target such as `.` with them, E2E-04), so `file_args` is true only for a tool
# known to take file paths: eslint, ruff (check / format), prettier, black, isort,
# flake8, pylint, gofmt, biome (check / lint / format), stylelint, rubocop; mypy and pyright only when the command already
# names a path. Everything else is false, the safe side: PostToolUse skips it and the Stop hook runs the whole verb once.
# That covers whole-project commands (tsc --noEmit, go vet ./..., golangci-lint run, mvn / gradle tasks, cargo clippy,
# make and npm run targets) and any command this table does not know. A package.json script body (`wrapped`) that already
# names a whole-repo target (`eslint .`) is false too: `npm run lint -- <file>` can only append, so the edit would lint the
# whole repository again (E2E-04).
_FILE_TOOLS = {"eslint", "prettier", "black", "isort", "flake8", "pylint", "gofmt", "stylelint", "rubocop"}
_FILE_TOOLS_SUB = {"ruff": {"check", "format"}, "biome": {"check", "lint", "format"}}
_PATH_IF_GIVEN = {"mypy", "pyright"}
_WRAPPERS = {"npx", "pnpm", "yarn", "bunx", "bun", "uv", "poetry", "pipenv", "exec", "run", "dlx", "python", "python3", "-m", "pipx"}
_SHELL_JOIN = re.compile(r"&&|\|\||;|\||\$\(|`|>|<")
_REPO_TARGETS = {".", "./", "./..."}


def file_args_for(text, wrapped=False):
    """True when the lint command `text` (a command line, or with `wrapped` a package.json script body that the verb
    can only append to) accepts file paths in place of its whole-repo target."""
    if not isinstance(text, str) or not text.strip() or _SHELL_JOIN.search(text):
        return False
    try:
        toks = shlex.split(text)
    except ValueError:
        return False
    while toks and (re.match(r"^[A-Za-z_][A-Za-z0-9_]*=", toks[0]) or toks[0] in _WRAPPERS):
        toks = toks[1:]
    if not toks:
        return False
    tool, rest = os.path.basename(toks[0]), toks[1:]
    if wrapped and any(t in _REPO_TARGETS for t in rest):
        return False
    if tool in _FILE_TOOLS:
        return True
    if tool in _FILE_TOOLS_SUB:
        return bool(rest) and rest[0] in _FILE_TOOLS_SUB[tool]
    if tool in _PATH_IF_GIVEN:
        return any(not t.startswith("-") for t in rest)
    return False


def lint_scope(cmd, eco, body=None):
    """{'exts': [...], 'file_args': bool} for a lint command in ecosystem `eco`. `body` is the text to classify when the
    command is a wrapper around it (the package.json script behind `npm run lint`). Unknown ecosystem: no exts."""
    out = {}
    exts = EXT.get(eco)
    if exts:
        out["exts"] = list(exts)
    out["file_args"] = file_args_for(body if body is not None else cmd)
    return out


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
        verbs["lint"]["file_args"] = file_args_for(scripts["lint"], wrapped=True)
    else:
        for cfg in ("eslint.config.js", "eslint.config.mjs", "eslint.config.cjs", "eslint.config.ts", ".eslintrc.js",
                    ".eslintrc.cjs", ".eslintrc.json", ".eslintrc.yml", ".eslintrc.yaml", ".eslintrc"):
            if _exists(root, cfg):
                verbs["lint"] = _verb(f"{exe} eslint {owned_exclude('eslint')} .", 2, cfg, files + [cfg], verb="lint")
                break
        else:
            if _exists(root, "biome.json") or _exists(root, "biome.jsonc"):
                cfg = "biome.json" if _exists(root, "biome.json") else "biome.jsonc"
                verbs["lint"] = _verb(f"{exe} biome check .", 2, cfg, files + [cfg], verb="lint")
                verbs["lint"]["_scope"] = True         # `.` becomes the manifest src/tests paths in scan() (E2E-01)
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
        verbs["lint"] = _verb(tool(f"ruff check {owned_exclude('ruff')} ."), 1, "ruff configuration", files + [f for f in ("ruff.toml", ".ruff.toml") if _exists(root, f)], verb="lint")
    elif _exists(root, ".flake8") or "[flake8]" in cfg or "[flake8]" in tox or has_dep("flake8"):
        verbs["lint"] = _verb(tool(f"flake8 {owned_exclude('flake8')}"), 2, "flake8 configuration", files + [f for f in (".flake8",) if _exists(root, f)], verb="lint")
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


def _git_run(root, *args):
    import subprocess
    try:
        r = subprocess.run(["git", "-C", root] + list(args), capture_output=True, timeout=10)
    except (OSError, subprocess.SubprocessError):
        return None
    return r


def _in_git(root):
    r = _git_run(root, "rev-parse", "--is-inside-work-tree")
    return bool(r is not None and r.returncode == 0 and r.stdout.strip() == b"true")


def _git_ignored(root, rel):
    """E2E-33 [design judgment]: True only when a TRACKED ignore file outside `rel/` ignores `rel/`. `git check-ignore -v` names
    the winning rule's source; a source that is untracked (a `dist/.gitignore` with `*` that `uv build` writes), inside the
    candidate itself, `.git/info/exclude` or a global excludes file differs per machine and per build, so it is no evidence.
    The result is then a function of the committed state: the same before and after a proof run, on every machine."""
    r = _git_run(root, "check-ignore", "-v", "--", rel + "/")
    if r is None or r.returncode != 0:
        return False
    line = r.stdout.decode("utf-8", "replace").splitlines()[0] if r.stdout else ""
    src, _, rest = line.partition(":")
    pattern = rest.partition(":")[2].split("\t", 1)[0]
    if not src or pattern.startswith("!") or os.path.isabs(src) or src.startswith(".git/"):
        return False
    src = src.replace(os.sep, "/")
    if src == rel or src.startswith(rel.rstrip("/") + "/"):
        return False
    t = _git_run(root, "ls-files", "--error-unmatch", "--", src)
    return t is not None and t.returncode == 0


def _tracked_dir(root, rel):
    """E2E-33: the directory holds a tracked file (`git ls-files -- <rel>` is not empty); outside git: the directory exists."""
    if not _in_git(root):
        return os.path.isdir(os.path.join(root, rel))
    r = _git_run(root, "ls-files", "--", rel)
    return bool(r is not None and r.returncode == 0 and r.stdout.strip())


def _go_test_dirs(root, limit=8):
    out = []
    for base, dirs, files in os.walk(root):
        rel = os.path.relpath(base, root).replace(os.sep, "/")
        dirs[:] = sorted(d for d in dirs if d not in SKIP_DIRS and not d.startswith(".") and d not in ("vendor", "testdata"))
        if rel.count("/") >= 4:
            dirs[:] = []
        if rel != "." and any(f.endswith("_test.go") for f in files):    # a repo-root test file has no directory to name
            out.append(rel)
            if len(out) >= limit:
                break
    return out


def _paths(root, eco):
    def entries(names):
        # gitignored first: a build output that a proof run creates (dist/ after `npm run build`) keeps the same proof
        # label, so the first update after init changes nothing (V1-19) [design judgment]. E2E-33: both proofs read the
        # tracked state only (a tracked ignore rule; a tracked file in the directory), so an untracked build output or its
        # own `.gitignore` never changes the result
        out = []
        for n in names:
            if eco is not None and _git_ignored(root, n):
                out.append({"path": n, "proof": "gitignored"})
            elif _tracked_dir(root, n):
                out.append({"path": n, "proof": "exists"})
        return out
    src = [d for d in ("src", "lib", "app", "pkg", "cmd", "internal") if os.path.isdir(os.path.join(root, d))]
    if eco and eco["id"] == "python" and not src:
        for n in sorted(os.listdir(root)):
            if n not in SKIP_DIRS and not n.startswith(".") and n not in ("tests", "test") and os.path.isfile(os.path.join(root, n, "__init__.py")):
                src.append(n)
    tests = [d for d in ("tests", "test", "__tests__", "spec", "src/test") if os.path.isdir(os.path.join(root, d))]
    if eco and eco["id"] == "go":    # Go keeps tests next to the code: every directory with a *_test.go file is a test location
        for d in _go_test_dirs(root):
            if d not in tests:
                tests.append(d)
    gen = entries(eco["generated"]) if eco else []
    # E2E-33: vendor directories by the same tracked-state rule (an installed, untracked vendor/bundle is no evidence)
    vendor = [{"path": n, "proof": "exists"} for n in ((eco["vendor"] if eco else []) + ["third_party"]) if _tracked_dir(root, n)]
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
    paths = _paths(root, primary)
    for spec in verbs.values():
        if spec.pop("_scope", False):         # a tool with no additive exclude option runs on the manifest paths (E2E-01)
            named = list(dict.fromkeys(p["path"] for k in ("src", "tests") for p in paths[k]))
            named = [n for n in named if not any(n.startswith(o + "/") for o in named)]    # src/test is inside src
            if named and spec["cmd"].endswith(" ."):
                spec["cmd"] = spec["cmd"][:-1] + " ".join(shlex.quote(n) for n in named)
    if primary:
        for v, spec in _make_targets(root).items():
            if v not in verbs or verbs[v]["evidence"]["rank"] > 2:
                verbs[v] = spec
    if primary and "lint" in verbs:
        for k, val in lint_scope(verbs["lint"]["cmd"], primary["id"]).items():
            verbs["lint"].setdefault(k, val)
        if verbs["lint"]["cmd"].startswith("make "):
            verbs["lint"]["file_args"] = False       # a Makefile target is a black box: its recipe may be project-wide
    secret = _secret_scanner(root) if primary else None
    inner = [v for v in ("lint", "typecheck", "test", "build") if v in verbs and verbs[v]["tier"] == "inner"]
    if secret:
        verbs["verify"] = secret
    elif inner:
        verbs["verify"] = _verb(".agents/bin/verb verify", 4, "framework", [".agents/manifest.yaml"], verb="verify")
    return {"ecosystems": [e["id"] for e in found], "primary": primary["id"] if primary else None,
            "labels": [e["label"] for e in found], "files": sorted({f for e in found for f in e["files"]}),
            "verbs": verbs, "paths": paths, "stated": None if found else _stated_stack(root),
            "secret_scanner": bool(secret)}
