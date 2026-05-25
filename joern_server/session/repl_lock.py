"""Context manager for the single-threaded Joern REPL semaphore."""

from __future__ import annotations

import threading
from contextlib import contextmanager
from typing import Iterator


@contextmanager
def repl_lock(semaphore: threading.Semaphore) -> Iterator[None]:
    """Acquire the per-replica REPL semaphore for the duration of the block."""
    semaphore.acquire()
    try:
        yield
    finally:
        semaphore.release()
