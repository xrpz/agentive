# agentive-unit: selftest.component
# design: spec.framework_repo.self_tests (component_tests), docs/impl/D.md (E2E-07)
"""Source lint with the standard library only (ruff is not a dev requirement): no Python source under src/ or tools/
defines the same name twice in one scope (ruff F811). E2E-07: a second `def pull` in review/github.py silently replaced
the first, and `agentive-review ci` crashed on every open PR in a real GitHub Actions run."""
import ast
import os
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
DIRS = ("src", "tools")
ACCESSORS = ("setter", "getter", "deleter")


def python_sources():
    """*.py files and extensionless scripts whose first line is a python shebang (the .agents/bin entry points)."""
    for d in DIRS:
        for base, dirs, files in os.walk(os.path.join(ROOT, d)):
            dirs[:] = sorted(x for x in dirs if x != "__pycache__")
            for f in sorted(files):
                path = os.path.join(base, f)
                if f.endswith(".py"):
                    yield path
                elif "." not in f:
                    with open(path, "rb") as fh:
                        first = fh.readline()
                    if first.startswith(b"#!") and b"python" in first:
                        yield path


def _allowed(node):
    """@x.setter / @x.getter / @x.deleter and @overload / @typing.overload redefine a name on purpose."""
    for d in node.decorator_list:
        if isinstance(d, ast.Attribute) and (d.attr in ACCESSORS or d.attr == "overload"):
            return True
        if isinstance(d, ast.Name) and d.id == "overload":
            return True
    return False


def redefinitions(tree):
    """[(scope, name, first_line, line)] for a def or class that repeats a def or class name of the same body."""
    out = []

    def scan(body, scope):
        seen = {}
        for node in body:
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                continue
            if node.name in seen and not _allowed(node):
                out.append((scope, node.name, seen[node.name], node.lineno))
            seen[node.name] = node.lineno
            if isinstance(node, ast.ClassDef):
                scan(node.body, node.name)

    scan(tree.body, "<module>")
    return out


class NoRedefinitionTest(unittest.TestCase):
    def test_no_name_is_defined_twice_in_one_scope(self):
        problems, count = [], 0
        for path in python_sources():
            rel = os.path.relpath(path, ROOT)
            with open(path, encoding="utf-8") as f:
                text = f.read()
            try:
                tree = ast.parse(text, rel)
            except SyntaxError as e:
                problems.append(f"{rel}: does not parse: {e}")
                continue
            count += 1
            problems += [f"{rel}:{line}: {scope}.{name} redefines line {first} (rename one; the later one wins silently)"
                         for scope, name, first, line in redefinitions(tree)]
        self.assertGreater(count, 20)
        self.assertEqual(problems, [], "\n".join(problems))

    def test_the_check_sees_a_redefinition(self):
        src = "def pull(s, n):\n    return 1\n\n\ndef pull(s, n):\n    return None\n\n\nclass C:\n    @property\n    def x(self):\n" \
              "        return 1\n\n    @x.setter\n    def x(self, v):\n        pass\n"
        self.assertEqual(redefinitions(ast.parse(src)), [("<module>", "pull", 1, 5)])


if __name__ == "__main__":
    unittest.main()
