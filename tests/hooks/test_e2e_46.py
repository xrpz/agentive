# agentive-unit: selftest.component
# design: spec.init_apply.session_start, spec.verb_wrapper, docs/impl/decisions-log.md §29
"""E2E-46 (b), the SessionStart hook: on an interpreter older than the python floor it adds the engine's line
`AGENTIVE_ERROR python3 < 3.13; NEXT=install python3 >= 3.13` to the session context; on 3.13 it adds nothing."""
import json
import os
import stat
import subprocess
import sys
import tempfile
import unittest

import test_hooks as th


class SessionFloor(th.Repo):
    def start(self, minor):
        d = tempfile.mkdtemp(prefix="agentive-oldpy-")
        self.addCleanup(subprocess.run, ["rm", "-rf", d])
        py = os.path.join(d, "python3")
        with open(py, "w", encoding="utf-8") as f:
            f.write(f"#!{sys.executable}\nimport runpy, sys\nsys.version_info = (3, {minor}, 0, 'final', 0)\n"
                    "sys.argv = sys.argv[1:]\nrunpy.run_path(sys.argv[0], run_name='__main__')\n")
        os.chmod(py, os.stat(py).st_mode | stat.S_IXUSR)
        inp = json.dumps({"cwd": self.repo, "session_id": "s1", "source": "startup"})
        r = subprocess.run([py, os.path.join(self.repo, ".agents", "hooks", "session-start-notice.py")], input=inp, capture_output=True,
                           text=True, env=th.ENV, cwd=self.repo)
        self.assertEqual(r.returncode, 0, r.stderr)
        return json.loads(r.stdout)["hookSpecificOutput"]["additionalContext"].splitlines()

    def test_e2e_46_session_start_names_the_python_floor_on_an_older_interpreter(self):
        old = self.start(12)
        self.assertTrue(old[0].startswith("AGENTIVE_STATE=ready;"), old)
        self.assertEqual(old[1], "AGENTIVE_ERROR python3 < 3.13; NEXT=install python3 >= 3.13")
        self.assertNotIn("AGENTIVE_ERROR python3 < 3.13; NEXT=install python3 >= 3.13", self.start(13))


if __name__ == "__main__":
    unittest.main()
