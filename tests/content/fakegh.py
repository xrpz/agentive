# agentive-unit: selftest.component
# design: docs/IMPLEMENTATION.md §6 (component tests: no network)
"""Fake `gh` for tests/content: answers `gh api ...` from FAKE_GH_ROUTES and logs every call to FAKE_GH_LOG.

A route is [regex over the space-joined arguments after `api`, {"stdout": str, "rc": int}]; the first match wins.
`-F body=@<file>` arguments are logged with the file content. No route -> exit 1 with "HTTP 404".
"""
import json
import os
import re
import sys


def main(argv):
    args = argv[1:] if argv and argv[0] == "api" else argv
    joined = " ".join(args)
    entry = {"args": args}
    for a in args:
        if a.startswith("body=@"):
            with open(a[len("body=@"):], encoding="utf-8") as f:
                entry["body"] = f.read()
    with open(os.environ["FAKE_GH_LOG"], "a", encoding="utf-8") as f:
        f.write(json.dumps(entry) + "\n")
    with open(os.environ["FAKE_GH_ROUTES"], encoding="utf-8") as f:
        routes = json.load(f)
    for pattern, resp in routes:
        if re.search(pattern, joined):
            sys.stdout.write(resp.get("stdout", ""))
            return int(resp.get("rc", 0))
    sys.stderr.write("gh: Not Found (HTTP 404)\n")
    return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
