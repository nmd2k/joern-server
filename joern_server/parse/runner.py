"""Subprocess wrapper for joern-parse."""

from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path


@dataclass
class ParseRunResult:
    ok: bool
    return_code: int
    stdout: str
    stderr: str


class ParseTimeoutError(Exception):
    def __init__(self, timeout_sec: int) -> None:
        self.timeout_sec = timeout_sec
        super().__init__(f"joern-parse timed out after {timeout_sec}s")


def run_joern_parse(
    parse_bin: str,
    src_dir: Path,
    cpg_out: Path,
    *,
    language: str = "",
    timeout_sec: int,
    jvm_xmx: str = "2g",
) -> ParseRunResult:
    cmd = [parse_bin, f"-J-Xmx{jvm_xmx}", "-J-XX:+UseContainerSupport", str(src_dir), "--output", str(cpg_out)]
    if language:
        cmd.extend(["--language", language])
    try:
        proc = subprocess.run(
            cmd,
            text=True,
            capture_output=True,
            timeout=timeout_sec,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise ParseTimeoutError(timeout_sec) from exc
    ok = proc.returncode == 0 and cpg_out.exists()
    return ParseRunResult(
        ok=ok,
        return_code=proc.returncode,
        stdout=proc.stdout[-100_000:],
        stderr=proc.stderr[-100_000:],
    )
