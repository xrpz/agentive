# Changesets (agent-facing)

Rule FM.version (design spec.framework_repo.versioning): every change adds ONE file `.changeset/<slug>.md`
in the same edit session. Never edit `package.json` "version", the plugin manifests or `CHANGELOG.md` by hand.

File format (the package name is always `agentive`; bump = patch | minor | major, semver):

```md
---
"agentive": patch
---

One or two sentences: what changed for users of the plugin or repo layer.
```

Bump guide [설계 판단]: patch = fix or wording; minor = new template, skill, hook, check or option;
major = a user repo must change something by hand after /agentive:update (renamed or removed installed file,
schema change that rejects existing files).

Release (the version step) is a U3 `release` commit: ask the author first. It runs `npm run version`
= `changeset version` (bumps package.json, writes CHANGELOG.md, deletes the consumed changesets)
followed by `python3 tools/generate.py` (copies the version into both plugin manifests and skills.lock).
The initial import is version 0.1.0 and has no changeset [설계 판단].
