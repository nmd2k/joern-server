import shutil
import threading
from pathlib import Path

# Per-hash locks to prevent concurrent parses of the same source hash.
_parse_hash_locks: dict[str, threading.Lock] = {}
_parse_hash_locks_lock = threading.Lock()


def get_hash_lock(source_hash: str) -> threading.Lock:
    with _parse_hash_locks_lock:
        if source_hash not in _parse_hash_locks:
            _parse_hash_locks[source_hash] = threading.Lock()
        return _parse_hash_locks[source_hash]


def cpg_copy(src: Path, dst: Path) -> None:
    """Copy a CPG — works for both file and directory layouts."""
    if src.is_file():
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(str(src), str(dst))
    else:
        shutil.copytree(str(src), str(dst))


def cpg_remove(path: Path) -> None:
    """Delete a CPG — works for both file and directory layouts."""
    if path.is_file():
        path.unlink(missing_ok=True)
    else:
        shutil.rmtree(path, ignore_errors=True)


def cpg_size_bytes(path: Path) -> int:
    """Return byte size of a CPG path — works for both file and directory layouts."""
    if path.is_file():
        return path.stat().st_size
    total = 0
    for f in path.rglob("*"):
        if f.is_file():
            try:
                total += f.stat().st_size
            except OSError:
                pass
    return total
