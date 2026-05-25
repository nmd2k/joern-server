import re

ANSI_RE = re.compile(r"\x1b\[[0-9;]*[a-zA-Z]")


def strip_ansi(text: str) -> str:
    """Strip ANSI color/style escape sequences from Joern REPL stdout."""
    return ANSI_RE.sub("", text) if text else text
