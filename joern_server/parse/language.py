# Map common/alias language names to the Joern-recognized language strings.
# Run `joern-parse --list-languages` inside the container to see all valid names.
_LANGUAGE_ALIASES: dict[str, str] = {
    # Python: 'python' triggers missing py2cpg.sh; 'pythonsrc' uses pysrc2cpg (installed).
    "py": "pythonsrc",
    "python": "pythonsrc",
    # JavaScript / TypeScript
    "js": "jssrc",
    "ts": "jssrc",
    "javascript": "jssrc",
    "typescript": "jssrc",
    # C++ — c2cpg handles both C and C++ when given a .cpp/.cc file.
    "cpp": "c",
    "c++": "c",
    "cc": "c",
    "cxx": "c",
    # C# — alias
    "cs": "csharpsrc",
    "csharp": "csharpsrc",
    # Go
    "go": "golang",
    # Java aliases
    "javasrc": "java",
    # Ruby
    "rb": "rubysrc",
    "ruby": "rubysrc",
}


def _normalize_language(language: str) -> str:
    """Translate caller-supplied language alias to the Joern-native name."""
    return _LANGUAGE_ALIASES.get(language.lower(), language) if language else language


_LANGUAGE_EXT: dict[str, str] = {
    "c": ".c",
    "cpp": ".cpp",
    "c++": ".cpp",
    "newc": ".c",
    "jssrc": ".js",
    "javascript": ".js",
    "typescript": ".ts",
    "pythonsrc": ".py",
    "python": ".py",
    "java": ".java",
    "javasrc": ".java",
    "rubysrc": ".rb",
    "ruby": ".rb",
    "php": ".php",
    "csharpsrc": ".cs",
    "csharp": ".cs",
    "swiftsrc": ".swift",
    "golang": ".go",
    "kotlin": ".kt",
    "rust": ".rs",
    "llvm": ".ll",
    "ghidra": ".c",
}


def _default_filename(language: str) -> str:
    """Return a filename with an extension appropriate for the language frontend."""
    normalized = _normalize_language(language)
    ext = _LANGUAGE_EXT.get(normalized, ".txt")
    return f"snippet{ext}"
