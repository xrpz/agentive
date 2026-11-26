# agentive-unit: lib.agentive
# design: decisions.accepted[TESTGUARD], spec.stop_hook, spec.review_pipeline.stages[RV1], spec.review_pipeline.stages[RV2] (owner B)
"""TESTGUARD: one deterministic detector for tests that were weakened to make a check pass. No model, no network.

The Stop hook (local: the working tree against the merge base) and RV1/RV2 (CI: the PR head against the PR base) call the same
`detect`, so a weakening the local hook reports is the one CI raises to a human. It reads two versions of every changed
file (base and head) and reports FACTS with `path:line`; it never judges intent. Kinds:

  deleted_test_file     a test file is deleted and the production file of the same stem is not (the old RV1 test-only-deletion rule)
  removed_test          a test function or case of the base version is missing in the head version (by name; a test whose body
                        reappears under another name or in another changed file is a move or rename, not a removal)
  skip_marker_added     a skip, only, xfail or ignore marker the base version did not have
  assertions_dropped    a test that exists in both versions holds fewer assertions in the head version
  config_deselect       a test-runner setting that deselects or relaxes was added (pytest addopts -k/--deselect/--ignore/-p no:,
                        collect_ignore, jest/vitest testPathIgnorePatterns and passWithNoTests, Maven/Gradle skipTests ...)
  coverage_lowered      a coverage threshold was lowered or removed (fail_under, --cov-fail-under, jest/vitest thresholds, JaCoCo minimum)

Languages (by file extension, name patterns only): Python (pytest, unittest), JavaScript and TypeScript (jest, vitest, mocha),
Go, Java and Kotlin (JUnit), Rust. A language with no test file in the diff costs nothing. The patterns are conservative on
purpose: a finding is a prompt for a human or for the author's explanation, not a verdict [design judgment].
"""
import os
import posixpath
import re
import subprocess

__all__ = ["detect", "detect_diff", "detect_local", "text", "summary", "fingerprint", "stem"]

ENV = dict(os.environ, LC_ALL="C", GIT_PAGER="cat", GIT_CONFIG_NOSYSTEM="1")
LANG_BY_EXT = {".py": "py", ".js": "js", ".jsx": "js", ".mjs": "js", ".cjs": "js", ".ts": "js", ".tsx": "js", ".mts": "js", ".cts": "js",
               ".go": "go", ".java": "java", ".kt": "java", ".rs": "rs"}
MAX_FILES = 400                 # a diff that touches more test or config files is read for the first 400 [design judgment]

# ---------------------------------------------------------------- patterns
PY_DEF = re.compile(r"^(\s*)(?:async\s+)?def\s+(test\w*)\s*\(")
PY_CLASS = re.compile(r"^(\s*)class\s+(\w+)")
JS_CASE = re.compile(r"""\b(?:it|test|xit|xtest|fit|ftest)(?:\.(?:each|concurrent|skip|only|todo|fails|failing)\b(?:\s*\([^)]*\)|`[^`]*`)?)*\s*\(\s*(['"`])((?:\\.|(?!\1).)*)\1""")
GO_FUNC = re.compile(r"^func\s+((?:Test|Benchmark|Fuzz)\w*)\s*\(")
GO_RUN = re.compile(r"""\bt\.Run\(\s*"((?:\\.|[^"\\])*)\"""")
JAVA_ANN = re.compile(r"^\s*@(?:Test|ParameterizedTest|RepeatedTest|TestFactory|TestTemplate|Property)\b")
JAVA_METHOD = re.compile(r"(?:\bvoid|\bfun)\s+(`[^`]+`|\w+)\s*\(")
RS_ATTR = re.compile(r"^\s*#\[(?:test|tokio::test|async_std::test|rstest|test_case)\b")
RS_FN = re.compile(r"^\s*(?:pub\s+)?(?:async\s+)?fn\s+(\w+)")

ASSERT = {
    "py": re.compile(r"^\s*assert\b|\bself\.(?:assert|fail)\w*\(|\bpytest\.(?:raises|warns|approx)\b|\.assert_\w+\(|\bassert_\w+\("),
    "js": re.compile(r"\bexpect\s*[(.]|\bassert\s*[.(]|\.should\b|\bassert\w*\("),
    "go": re.compile(r"\bt\.(?:Error|Errorf|Fatal|Fatalf|Fail|FailNow)\b|\b(?:assert|require)\.\w+\("),
    "java": re.compile(r"\bassert\w*\(|\bAssertions\.\w+\(|\bassertThat\b|\bverify\(|\bfail\("),
    "rs": re.compile(r"\b(?:debug_)?assert(?:_eq|_ne)?!"),
}
SKIP_MARKERS = {
    "py": re.compile(r"\bpytest\.mark\.(?:skip|skipif|xfail)\b|\bunittest\.(?:skip|skipIf|skipUnless|expectedFailure)\b|\bpytest\.(?:skip|xfail)\s*\(|^\s*@skip(?:If|Unless)?\b|\bself\.skipTest\s*\("),
    "js": re.compile(r"\b(?:it|test|describe|context|suite)\.(?:skip|only|todo|fixme)\b|\b(?:xit|xtest|xdescribe|fit|fdescribe)\s*\("),
    "go": re.compile(r"\bt\.Skip(?:f|Now)?\s*\("),
    "java": re.compile(r"@(?:Disabled|Ignore)\b|\bAssumptions?\.assume\w*\(|@Tag\(\s*\"(?:slow|flaky)\"\s*\)"),
    "rs": re.compile(r"#\[ignore\b"),
}
PYTEST_FILES = {"pytest.ini", "tox.ini", "setup.cfg", "pyproject.toml", "conftest.py"}
JS_CONFIG = re.compile(r"^(?:jest|vitest|vite|playwright|cypress|karma)\.(?:config|workspace)\.\w+$|^\.mocharc(?:\.\w+)?$")
JAVA_BUILD = {"pom.xml", "build.gradle", "build.gradle.kts"}
COVERAGE_FILES = {"pytest.ini", "tox.ini", "setup.cfg", "pyproject.toml", ".coveragerc", "package.json", "pom.xml", "build.gradle", "build.gradle.kts"}
DESELECT = {
    "pytest": re.compile(r"--deselect\b|--ignore(?:-glob)?\b|(?<![\w-])-p\s*no:|[\"'\s]-k\s+\S|-m\s+[\"']?not\b|^\s*collect_ignore(?:_glob)?\b"),
    "js": re.compile(r"\btestPathIgnorePatterns\b|\bpassWithNoTests\b|\btestIgnore\b|\btestPathIgnore\b|--passWithNoTests\b"),
    "java": re.compile(r"\bskipTests\b|\bmaven\.test\.skip\b|\btestFailureIgnore\b|\bignoreFailures\b"),
}
THRESHOLDS = [("fail_under", re.compile(r"\bfail_under\s*[=:]\s*([\d.]+)")),
              ("cov-fail-under", re.compile(r"--cov-fail-under[=\s]+([\d.]+)")),
              ("jacoco minimum", re.compile(r"<minimum>\s*([\d.]+)\s*</minimum>|\bminimum\s*=\s*([\d.]+)")),
              ("js threshold", re.compile(r"[\"']?\b(?:branches|functions|lines|statements)\b[\"']?\s*:\s*([\d.]+)"))]


# ---------------------------------------------------------------- small helpers
def stem(path):
    """The stem used to pair a test file with its production file (the old RV1 rule): test_x.py, x_test.go, X.test.ts, XTest.java -> x."""
    b = posixpath.basename(path)
    b = re.sub(r"\.(test|spec)(?=\.)", "", b)
    b = re.sub(r"^test_|_test(?=\.)|(Test|Tests|IT)(?=\.)", "", b)
    return b.split(".")[0].lower()


def _lang(path):
    return LANG_BY_EXT.get(posixpath.splitext(path)[1].lower())


def _comment(line, lang):
    s = line.strip()
    if lang == "py":
        return s.startswith("#") and not s.startswith("#[")
    if lang == "rs":
        return s.startswith("//")
    return s.startswith("//") or s.startswith("*") or s.startswith("/*")


def _norm(lines):
    return re.sub(r"\s+", " ", "\n".join(x.strip() for x in lines if x.strip())).strip()


def _brace_body(lines, i):
    """Lines of the block whose first `{` is on line i or within the next 3 lines (balanced braces, strings read crudely)."""
    depth, started, out = 0, False, []
    for j in range(i, len(lines)):
        if not started and j > i + 3:
            return lines[i:i + 1]
        for ch in re.sub(r"(\"(?:\\.|[^\"\\])*\"|'(?:\\.|[^'\\])*'|`[^`]*`)", "", lines[j]):
            if ch == "{":
                depth += 1
                started = True
            elif ch == "}":
                depth -= 1
        out.append(lines[j])
        if started and depth <= 0:
            return out
    return out


def _py_body(lines, i, indent):
    out = [lines[i]]
    for j in range(i + 1, len(lines)):
        ln = lines[j]
        if ln.strip() and len(ln) - len(ln.lstrip()) <= indent:
            break
        out.append(ln)
    return out


def tests_of(path, text):
    """[(key, line number, body lines)] of the test functions or cases in text, by the name patterns of the file's language."""
    lang = _lang(path)
    if not lang or text is None:
        return []
    lines = text.splitlines()
    found, seen = [], {}

    def add(key, i, body, bare):
        # the first line loses the test name, so a renamed test keeps the signature of its body (rename detection in detect())
        body = [body[0].replace(bare, "", 1)] + list(body[1:])
        n = seen.get(key, 0)
        seen[key] = n + 1
        found.append((key if n == 0 else f"{key}#{n + 1}", i + 1, body))

    if lang == "py":
        classes = []
        for i, ln in enumerate(lines):
            m = PY_CLASS.match(ln)
            if m:
                ind = len(m.group(1))
                classes = [c for c in classes if c[0] < ind] + [(ind, m.group(2))]
                continue
            m = PY_DEF.match(ln)
            if m:
                ind = len(m.group(1))
                classes = [c for c in classes if c[0] < ind]
                add(".".join([c[1] for c in classes] + [m.group(2)]), i, _py_body(lines, i, ind), m.group(2))
    elif lang == "js":
        for i, ln in enumerate(lines):
            if _comment(ln, lang):
                continue
            m = JS_CASE.search(ln)
            if m:
                add(m.group(2), i, _brace_body(lines, i), m.group(2))
    elif lang == "go":
        func = None
        for i, ln in enumerate(lines):
            m = GO_FUNC.match(ln)
            if m:
                func = m.group(1)
                add(func, i, _brace_body(lines, i), func)
                continue
            m = GO_RUN.search(ln)
            if m and func and not _comment(ln, lang):
                add(f"{func}/{m.group(1)}", i, _brace_body(lines, i), m.group(1))
    elif lang == "java":
        for i, ln in enumerate(lines):
            if JAVA_ANN.match(ln):
                for j in range(i, min(i + 6, len(lines))):
                    m = JAVA_METHOD.search(lines[j])
                    if m:
                        add(m.group(1).strip("`"), j, _brace_body(lines, j), m.group(1))
                        break
    elif lang == "rs":
        for i, ln in enumerate(lines):
            if RS_ATTR.match(ln):
                for j in range(i, min(i + 6, len(lines))):
                    m = RS_FN.match(lines[j])
                    if m:
                        add(m.group(1), j, _brace_body(lines, j), m.group(1))
                        break
    return found


def _count_asserts(lang, body):
    rx = ASSERT[lang]
    return sum(1 for ln in body if not _comment(ln, lang) and rx.search(ln))


def _markers(path, text):
    """[(line number, normalized line)] of the skip, only, xfail and ignore markers (comment lines left out)."""
    lang = _lang(path)
    if not lang or text is None:
        return []
    rx = SKIP_MARKERS[lang]
    return [(i + 1, re.sub(r"\s+", " ", ln.strip())) for i, ln in enumerate(text.splitlines())
            if not _comment(ln, lang) and rx.search(ln)]


def _config_kinds(path):
    b = posixpath.basename(path)
    kinds = []
    if b in PYTEST_FILES:
        kinds.append("pytest")
    if JS_CONFIG.match(b) or b == "package.json":
        kinds.append("js")
    if b in JAVA_BUILD:
        kinds.append("java")
    return kinds


def _is_config(path):
    b = posixpath.basename(path)
    return bool(_config_kinds(path)) or b in COVERAGE_FILES or b == ".coveragerc"


def _added_lines(base, head):
    """[(line number, text)] of the lines of head the base does not have (as a multiset of stripped lines)."""
    have = {}
    for ln in (base or "").splitlines():
        k = ln.strip()
        have[k] = have.get(k, 0) + 1
    out = []
    for i, ln in enumerate((head or "").splitlines()):
        k = ln.strip()
        if have.get(k, 0) > 0:
            have[k] -= 1
        else:
            out.append((i + 1, ln))
    return out


def _thresholds(path, text):
    """[(key, value, line)] of the coverage thresholds a config file holds. A JS threshold counts in jest/vitest config and package.json only."""
    b = posixpath.basename(path)
    out = []
    for i, ln in enumerate((text or "").splitlines()):
        s = ln.strip()
        if s.startswith("#") or s.startswith("//"):
            continue
        for key, rx in THRESHOLDS:
            if key == "js threshold" and not (JS_CONFIG.match(b) or b == "package.json"):
                continue
            if key == "jacoco minimum" and b not in JAVA_BUILD:
                continue
            if key in ("fail_under", "cov-fail-under") and b in JAVA_BUILD:
                continue
            for m in rx.finditer(ln):
                v = next((g for g in m.groups() if g), None)
                try:
                    out.append((key, float(v), i + 1))
                except (TypeError, ValueError):
                    pass
    return out


# ---------------------------------------------------------------- the detector
def detect(statuses, show_base, show_head, is_test):
    """statuses [(letter, path, old path or None)] as `git diff --name-status -M`; show_base(path) and show_head(path) -> text or None;
    is_test(path) -> bool. -> [{kind, path, line, detail}] ordered by path and line. Reads only test files, Rust files and test-runner
    config files."""
    rel = [(st, p, old) for st, p, old in statuses if st in "ADMRC" and (is_test(p) or _lang(p) == "rs" or _is_config(p))][:MAX_FILES]
    deleted = [p for st, p, _ in statuses if st == "D"]
    prod_deleted = {stem(p) for p in deleted if not is_test(p) and _lang(p) != "rs"}
    out = []
    removed, added_bodies = [], set()
    for st, p, old in rel:
        base_path = old or p
        base = None if st in "AC" else show_base(base_path)
        head = None if st == "D" else show_head(p)
        if st == "D":
            if is_test(p) and stem(p) not in prod_deleted:
                out.append({"kind": "deleted_test_file", "path": p, "line": 1, "detail": "the test file is deleted and the code it tests is not"})
            continue
        if head is None:
            continue
        test_like = is_test(p) or _lang(p) == "rs"
        if test_like and _lang(p):
            lang = _lang(p)
            bt, ht = tests_of(base_path, base), tests_of(p, head)
            hk = {k: (ln, body) for k, ln, body in ht}
            base_keys = {k for k, _, _ in bt}
            for k, _, body in ht:
                if k not in base_keys:                  # only a test that is new in the head can be the other half of a rename or move
                    added_bodies.add(_norm(body))
            gone = {key for key, _, _ in bt if key not in hk}
            for key, ln, body in bt:
                if key not in hk:
                    removed.append((p, key, ln, _norm(body)))
                elif not any(g.startswith(key + "/") for g in gone):      # a Go parent whose subtest is gone is reported by the subtest
                    before, after = _count_asserts(lang, body), _count_asserts(lang, hk[key][1])
                    if after < before:
                        out.append({"kind": "assertions_dropped", "path": p, "line": hk[key][0],
                                    "detail": f"test {key} holds {after} assertion(s), the base version had {before}"})
            have = {}
            for _, mk in _markers(base_path, base):
                have[mk] = have.get(mk, 0) + 1
            for ln, mk in _markers(p, head):
                if have.get(mk, 0) > 0:
                    have[mk] -= 1
                else:
                    out.append({"kind": "skip_marker_added", "path": p, "line": ln, "detail": mk[:120]})
        for kind in _config_kinds(p):
            rx = DESELECT[kind]
            for ln, t in _added_lines(base, head):
                s = t.strip()
                if s and not s.startswith("#") and not s.startswith("//") and rx.search(t):
                    out.append({"kind": "config_deselect", "path": p, "line": ln, "detail": re.sub(r"\s+", " ", s)[:120]})
        if _is_config(p) and base is not None:
            bth, hth = _thresholds(base_path, base), _thresholds(p, head)
            for key in sorted({k for k, _, _ in bth}):
                bv = [(v, ln) for k, v, ln in bth if k == key]
                hv = [(v, ln) for k, v, ln in hth if k == key]
                if len(hv) < len(bv):
                    out.append({"kind": "coverage_lowered", "path": p, "line": bv[len(hv)][1],
                                "detail": f"{key} threshold removed (line in the base version)"})
                for (b, _), (h, ln) in zip(bv, hv):
                    if h < b:
                        out.append({"kind": "coverage_lowered", "path": p, "line": ln, "detail": f"{key} {b:g} -> {h:g}"})
    for p, key, ln, body in removed:
        if body and body in added_bodies:
            continue                                  # the same body exists in the head: a rename or a move
        out.append({"kind": "removed_test", "path": p, "line": ln, "detail": f"test {key} is gone (line in the base version)"})
    return sorted(out, key=lambda f: (f["path"], f["line"], f["kind"]))


def text(f):
    return f"{f['path']}:{f['line']} {f['kind']}: {f['detail']}"


def summary(findings):
    """One short line for a tier reason: the count and the kinds."""
    kinds = {}
    for f in findings:
        kinds[f["kind"]] = kinds.get(f["kind"], 0) + 1
    return f"{len(findings)} fact(s): " + ", ".join(f"{n} {k}" for k, n in sorted(kinds.items()))


def fingerprint(findings):
    import hashlib
    return hashlib.sha256("\n".join(text(f) for f in findings).encode()).hexdigest()[:16]


# ---------------------------------------------------------------- readers
def detect_diff(diff, is_test):
    """CI: a review.gitio.Diff (PR base and head)."""
    return detect(diff.name_status(), diff.show_base, diff.show_head, is_test)


def _git(root, *args):
    r = subprocess.run(["git", "-C", root] + list(args), capture_output=True, env=ENV, timeout=60)
    return r.stdout.decode("utf-8", "replace") if r.returncode == 0 else None


def local_base(root):
    """The commit the local work is compared with: the merge base of HEAD and its upstream, else of HEAD and origin/HEAD,
    origin/main, origin/master, main, master, else HEAD itself (only the uncommitted change). None without a HEAD commit."""
    if _git(root, "rev-parse", "--verify", "-q", "HEAD^{commit}") is None:
        return None
    for ref in ("@{upstream}", "origin/HEAD", "origin/main", "origin/master", "main", "master"):
        sha = _git(root, "merge-base", "HEAD", ref)
        if sha and sha.strip():
            return sha.strip()
    return _git(root, "rev-parse", "HEAD").strip()


def detect_local(root, is_test):
    """Local (Stop hook): the working tree (tracked changes, staged or not, plus untracked files that are not ignored) against the
    merge base; unpushed commits count. [] outside git or on any git failure (the hook must never fail on this)."""
    try:
        base = local_base(root)
        if not base:
            return []
        out = _git(root, "diff", "--no-color", "-M", "--name-status", "-z", base)
        if out is None:
            return []
        parts, statuses, i = out.split("\0"), [], 0
        while i < len(parts) and parts[i]:
            letter = parts[i][0]
            if letter in "RC":
                statuses.append((letter, parts[i + 2], parts[i + 1]))
                i += 3
            else:
                statuses.append((letter, parts[i + 1], None))
                i += 2
        untracked = _git(root, "ls-files", "-z", "--others", "--exclude-standard") or ""
        statuses += [("A", p, None) for p in untracked.split("\0") if p]
        statuses = [s for s in statuses if not s[1].startswith(".agents/state/")]

        def show_head(path):
            try:
                with open(os.path.join(root, path), encoding="utf-8", errors="replace") as f:
                    return f.read()
            except OSError:
                return None

        return detect(statuses, lambda p: _git(root, "show", f"{base}:{p}"), show_head, is_test)
    except (OSError, subprocess.SubprocessError, ValueError):
        return []
