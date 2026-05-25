import re
from pathlib import Path
from typing import Optional


def sample_id_from_cpg_path(path: str) -> Optional[str]:
    """Extract sample_id from /workspace/cpg-out/<sample_id> style paths."""
    p = (path or "").strip().rstrip("/")
    if not p:
        return None
    parts = Path(p).parts
    for i, part in enumerate(parts):
        if part == "cpg-out" and i + 1 < len(parts):
            return safe_sample_id(parts[i + 1])
    return safe_sample_id(Path(p).name) if p else None


def safe_sample_id(raw: str) -> str:
    safe = re.sub(r"[^a-zA-Z0-9._-]", "_", raw.strip())
    return safe or "sample"


def joern_hash_sidecar(cpg_out: Path) -> Path:
    """Path to source-hash sidecar for a CPG output path (file or directory)."""
    return Path(str(cpg_out) + ".joern_hash")


def cpg_paths_equal(a: str, b: str) -> bool:
    """Compare CPG filesystem paths (file or directory)."""
    left = (a or "").strip()
    right = (b or "").strip()
    if not left or not right:
        return False
    if left.rstrip("/") == right.rstrip("/"):
        return True
    try:
        return Path(left).resolve() == Path(right).resolve()
    except OSError:
        return False
