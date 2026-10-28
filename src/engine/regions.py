# agentive-unit: eng.apply
# design: spec.init_apply.managed_regions (markers, provenance, hash, write_rules), UV-20, UV-27
"""Managed START/END regions in shared files (AGENTS.md, CLAUDE.md, code_review.md, .gitignore).

- markers: Markdown `<!-- AGENTIVE START <region> -->` / `<!-- AGENTIVE END <region> -->`; hash comment
  `# AGENTIVE START <region>` / `# AGENTIVE END <region>` for yaml, toml, shell and .gitignore files
- provenance: first line inside the region, a comment (not instruction text)
- body hash: lines strictly between the markers minus the provenance line, LF-normalized, trailing whitespace
  of every line trimmed and trailing blank lines dropped (UV-20); verified date and commit are outside the hash
- bytes outside the markers are preserved exactly (line endings, BOM, final newline: UV-27); the region's own
  lines use the line ending of its START line
"""
import hashlib
import re

__all__ = ["style_for", "find", "body_hash", "text_hash", "provenance", "render_region", "splice", "append", "Malformed"]

PROV_RE = re.compile(r"^\s*(<!--\s*)?#?\s*Verified \d{4}-\d{2}-\d{2} against \S+\. Managed by agentive")


class Malformed(Exception):
    pass


def style_for(path):
    p = path.lower()
    return "md" if p.endswith((".md", ".markdown", ".mdx")) else "hash"


def markers(style, region):
    if style == "md":
        return f"<!-- AGENTIVE START {region} -->", f"<!-- AGENTIVE END {region} -->"
    return f"# AGENTIVE START {region}", f"# AGENTIVE END {region}"


def provenance(style, date, commit):
    text = (f"Verified {date} against {commit or 'none'}. Managed by agentive; edits inside this region are reported by "
            "/agentive:update and replaced only with --force or when you accept them. Keep text you want preserved outside the markers.")
    return f"<!-- {text} -->" if style == "md" else f"# {text}"


def _lines(text):
    return text.splitlines(keepends=True)


def find(text, style, region):
    """(start_idx, end_idx) line indexes of the START and END marker lines, or None when absent.
    Raises Malformed unless there is exactly one START and one END for the region, START before END, and no other
    agentive region marker opens between them (no nesting or overlap)."""
    s_m, e_m = markers(style, region)
    lines = _lines(text)
    starts = [i for i, ln in enumerate(lines) if ln.strip() == s_m]
    ends = [i for i, ln in enumerate(lines) if ln.strip() == e_m]
    if not starts and not ends:
        return None
    if len(starts) != 1 or len(ends) != 1 or starts[0] > ends[0]:
        raise Malformed(f"region {region}: {len(starts)} START and {len(ends)} END marker(s)" + (", START after END" if starts and ends and starts[0] > ends[0] else ""))
    opener = re.compile(r"^\s*(<!--\s*)?#?\s*AGENTIVE (START|END) ")
    for ln in lines[starts[0] + 1:ends[0]]:
        if opener.match(ln):
            raise Malformed(f"region {region}: another agentive marker inside it (nesting or overlap)")
    return starts[0], ends[0]


def _norm_body(lines):
    out = [ln.rstrip("\r\n").rstrip() for ln in lines]
    out = [ln for ln in out]
    while out and not out[-1]:
        out.pop()
    while out and not out[0]:
        out.pop(0)
    return "\n".join(out)


def text_hash(body_text):
    """Hash of a rendered region body (same normalization as body_hash)."""
    return hashlib.sha256(_norm_body(_lines(body_text)).encode("utf-8")).hexdigest()


def current_body(text, span):
    lines = _lines(text)
    inner = lines[span[0] + 1:span[1]]
    if inner and PROV_RE.match(inner[0]):
        inner = inner[1:]
    return "".join(inner)


def body_hash(text, span):
    lines = _lines(text)
    inner = lines[span[0] + 1:span[1]]
    if inner and PROV_RE.match(inner[0]):
        inner = inner[1:]
    return hashlib.sha256(_norm_body(inner).encode("utf-8")).hexdigest()


def current_provenance(text, span):
    lines = _lines(text)
    inner = lines[span[0] + 1:span[1]]
    if inner and PROV_RE.match(inner[0]):
        m = re.search(r"Verified (\d{4}-\d{2}-\d{2}) against (\S+?)\.", inner[0])
        if m:
            return m.group(1), (None if m.group(2) == "none" else m.group(2))
    return None, None


def render_region(style, region, body, date, commit, nl="\n"):
    s_m, e_m = markers(style, region)
    body_lines = _norm_body(_lines(body)).split("\n") if body.strip() else []
    out = [s_m, provenance(style, date, commit)] + body_lines + [e_m]
    return nl.join(out) + nl


def splice(text, span, block):
    """Replace marker lines span[0]..span[1] (inclusive) with block; everything else byte-identical."""
    lines = _lines(text)
    end_line = lines[span[1]]
    trailing_nl = end_line.endswith("\n")
    if not trailing_nl and block.endswith("\n"):
        block = block[:-2] if block.endswith("\r\n") else block[:-1]
    return "".join(lines[:span[0]]) + block + "".join(lines[span[1] + 1:])


def line_ending(text, span):
    ln = _lines(text)[span[0]]
    return "\r\n" if ln.endswith("\r\n") else "\n"


def append(text, block):
    """Append a region at the end of the file after one blank line (absent file: only the region)."""
    if not text:
        return block
    nl = "\r\n" if "\r\n" in text else "\n"
    if nl != "\n":
        block = block.replace("\n", nl)
    if not text.endswith(("\n", "\r\n")):
        text += nl
    return text + nl + block
