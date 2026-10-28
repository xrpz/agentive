"""agentive self-test fixture: a tiny Python package."""


def slugify(text: str) -> str:
    """Lowercase words joined by '-'."""
    words = "".join(c if c.isalnum() else " " for c in text).split()
    return "-".join(w.lower() for w in words)
