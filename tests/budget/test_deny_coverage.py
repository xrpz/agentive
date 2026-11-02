# agentive-unit: selftest.size_budget
# design: spec.framework_repo.self_tests, spec.framework_repo.editing_rules
"""Every generated output of this repo is covered by an `Edit(...)` deny rule in .claude/settings.json [설계 판단].

AGENTS.md rule 1 says generated outputs are never edited by hand and that `.claude/settings.json` denies those edits. The deny list
is hand-maintained, so this check compares it with the generator's own output list (`python3 tools/generate.py --list`): a generated
file that no rule covers fails the size_budget step (always part of --fast and of CI) and the message names the exact line to add.
Edit deny rules also cover Write and NotebookEdit (Claude Code permissions page), so only `Edit(...)` rules are used and required.

Rule syntax checked: `Edit(/<path>)`, a path from the project root; `*` stays inside one path segment, `**` crosses segments.
Not generated in full, therefore no rule (explicit exceptions): see PARTLY_GENERATED."""
import json
import os
import re
import subprocess
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
SETTINGS = os.path.join(ROOT, ".claude", "settings.json")
# Generated outputs that stay editable because the file is only partly generated: the generator writes one region, the rest is
# hand-written. AGENTS.md carries the generated install-sequence region (AGENTS.md rule 6: keep it as the generator writes it).
PARTLY_GENERATED = {"AGENTS.md"}
# Written by the version step, not by tools/generate.py; AGENTS.md says "never edit by hand", so the deny list must name them too.
VERSION_STEP_FILES = ["CHANGELOG.md", "package-lock.json"]


def say(msg):
    sys.stderr.write("SIZE " + msg + "\n")


def rule_regex(pattern):
    """`/plugins/**` -> regex over a root-relative path; `*` does not cross `/`, `**` does."""
    body = pattern.lstrip("/")
    out, i = "", 0
    while i < len(body):
        if body.startswith("**", i):
            out += ".*"
            i += 2
        elif body[i] == "*":
            out += "[^/]*"
            i += 1
        else:
            out += re.escape(body[i])
            i += 1
    return re.compile("^" + out + "$")


def edit_rules(deny):
    return [m.group(1) for d in deny for m in [re.fullmatch(r"Edit\((/[^)]*)\)", d)] if m]


def uncovered(outputs, deny, partly=PARTLY_GENERATED, extra=VERSION_STEP_FILES):
    """-> the generated (and version-step) paths no Edit deny rule covers, as the `Edit(/path)` line to add."""
    regexes = [rule_regex(r) for r in edit_rules(deny)]
    paths = sorted((set(outputs) - set(partly)) | set(extra))
    return [f"Edit(/{p})" for p in paths if not any(rx.match(p) for rx in regexes)]


_OUTPUTS = []   # `generate.py --list` takes 0.8 s and its answer does not change during a run: ask once per process [설계 판단]


def generated_outputs():
    if not _OUTPUTS:
        _OUTPUTS.extend(_list_outputs())
    return list(_OUTPUTS)


def _list_outputs():
    r = subprocess.run([sys.executable, "tools/generate.py", "--list"], cwd=ROOT, capture_output=True, text=True)
    if r.returncode != 0:
        raise RuntimeError("tools/generate.py --list failed:\n" + (r.stdout + r.stderr)[-2000:])
    return [ln.split("\t")[0] for ln in r.stdout.splitlines() if ln.strip()]


def settings_deny():
    with open(SETTINGS, encoding="utf-8") as f:
        return json.load(f)["permissions"]["deny"]


class DenyCoversGeneratedOutputs(unittest.TestCase):
    def test_every_generated_output_has_an_edit_deny_rule(self):
        outputs, deny = generated_outputs(), settings_deny()
        missing = uncovered(outputs, deny)
        say(f"deny list: {len(edit_rules(deny))} Edit rules cover {len(set(outputs) - PARTLY_GENERATED)} generated files "
            f"(+ {', '.join(VERSION_STEP_FILES)}); partly generated, left editable: {', '.join(sorted(PARTLY_GENERATED))}")
        self.assertEqual(missing, [], "add to permissions.deny in .claude/settings.json (hand-maintained list; confirm path, ask the "
                                      "author first): " + ", ".join(missing))

    def test_the_exceptions_are_real_generated_outputs(self):
        self.assertLessEqual(PARTLY_GENERATED, set(generated_outputs()), "a PARTLY_GENERATED entry is no longer a generated output: drop it")


class Matching(unittest.TestCase):
    OUT = ["plugins/claude-code/agentive/hooks/hooks.json", "docs/map/index.html", "docs/DECISIONS.md", "design/INDEX.md", "AGENTS.md",
           ".claude-plugin/marketplace.json", ".agents/plugins/marketplace.json"]
    FULL = ["Edit(/plugins/**)", "Edit(/docs/map/**)", "Edit(/docs/DECISIONS.md)", "Edit(/design/INDEX.md)",
            "Edit(/.claude-plugin/marketplace.json)", "Edit(/.agents/plugins/marketplace.json)", "Edit(/CHANGELOG.md)",
            "Edit(/package-lock.json)"]

    def test_a_complete_list_passes(self):
        self.assertEqual(uncovered(self.OUT, self.FULL), [])

    def test_a_removed_rule_fails_and_names_the_line(self):
        for gone, line in (("Edit(/design/INDEX.md)", "Edit(/design/INDEX.md)"), ("Edit(/plugins/**)", "Edit(/plugins/claude-code/agentive/hooks/hooks.json)"),
                           ("Edit(/CHANGELOG.md)", "Edit(/CHANGELOG.md)")):
            deny = [d for d in self.FULL if d != gone]
            self.assertEqual(uncovered(self.OUT, deny), [line], gone)

    def test_the_partly_generated_file_needs_no_rule_but_others_do(self):
        self.assertEqual(uncovered(["AGENTS.md"], self.FULL), [])
        self.assertEqual(uncovered(["AGENTS.md"], self.FULL, partly=set()), ["Edit(/AGENTS.md)"])

    def test_only_edit_rules_count_and_star_stays_in_one_segment(self):
        self.assertEqual(uncovered(["docs/DECISIONS.md"], ["Write(/docs/DECISIONS.md)"], extra=[]), ["Edit(/docs/DECISIONS.md)"])
        self.assertEqual(uncovered(["docs/map/a/b.html"], ["Edit(/docs/map/*)"], extra=[]), ["Edit(/docs/map/a/b.html)"])
        self.assertEqual(uncovered(["docs/map/a/b.html"], ["Edit(/docs/map/**)"], extra=[]), [])


if __name__ == "__main__":
    unittest.main()
