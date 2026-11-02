# agentive-unit: selftest.component
# design: docs/IMPLEMENTATION.md §6 (component tests: no network)
"""Fake `gh` for tests/content: answers `gh api ...` from FAKE_GH_ROUTES and logs every call to FAKE_GH_LOG.

A route is [regex over the space-joined arguments after `api`, {"stdout": str, "rc": int}]; the first match wins.
A route may carry "seq": [{"stdout": ..., "rc": ...}, ...]: the n-th call that matches it gets the n-th entry (the last one repeats).
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
    with open(os.environ["FAKE_GH_LOG"], encoding="utf-8") as f:
        earlier = [json.loads(x)["args"] for x in f if x.strip()][:-1]
    for pattern, resp in routes:
        if re.search(pattern, joined):
            if "seq" in resp:
                n = sum(1 for a in earlier if re.search(pattern, " ".join(a)))
                resp = resp["seq"][min(n, len(resp["seq"]) - 1)]
            sys.stdout.write(resp.get("stdout", ""))
            return int(resp.get("rc", 0))
    sys.stderr.write("gh: Not Found (HTTP 404)\n")
    return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
