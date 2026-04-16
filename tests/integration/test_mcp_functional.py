#!/usr/bin/env python3
"""
Functional test for all 18 Joern MCP tools.

NOT a stress test - one carefully selected CPG, every tool verified,
empty/error results diagnosed with follow-up CPGQL queries.

Result status per tool:
  [PASS]  non-empty, meaningful output
  [EMPTY] tool ran but returned nothing (with debug explanation)
  [FAIL]  Python exception / server error
  [SKIP]  required ID was not discoverable for this CPG

Run (from repo root):
  pytest tests/integration/test_mcp_functional.py -v

Options:
  --http-url   http://localhost:8080
  --mcp-url    http://localhost:9000/sse
  --sven       /path/to/sven_preprocessed_v2.jsonl
  --sample-id  <hex>  (skip selection, use this ID directly)
  --report     mcp_functional_report.md
"""

from __future__ import annotations

import argparse
import asyncio
import json
import re
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import httpx

# -- MCP client ----------------------------------------------------------------
try:
    from fastmcp import Client as MCPClient
    from fastmcp.client.transports import SSETransport
    _MCP_AVAILABLE = True
except ImportError:
    _MCP_AVAILABLE = False

ANSI_RE = re.compile(r'\x1b\[[0-9;]*[mK]')


def strip_ansi(s: str) -> str:
    return ANSI_RE.sub('', s)


def extract_repl_value(raw: str) -> str:
    """Remove ANSI codes and the 'val resN: Type = ' prefix from Joern output."""
    s = strip_ansi(raw).strip()
    if ' = ' in s:
        s = s.split(' = ', 1)[-1].strip()
    return s


def parse_int(raw: str) -> int:
    m = re.search(r'=\s*(\d+)', strip_ansi(raw))
    return int(m.group(1)) if m else 0


def parse_list(raw: str) -> List[str]:
    """Parse Joern List(...) output into Python list of strings."""
    val = extract_repl_value(raw)
    if not val or val in ('List()', ''):
        return []
    # Strip outer List(...)
    if val.startswith('List(') and val.endswith(')'):
        val = val[5:-1]
    # Extract quoted strings and identifiers (handles IDs, full names, etc.)
    items = re.findall(r'"((?:[^"\\]|\\.)*)"', val)
    if items:
        return items
    # Fallback: bare tokens (for numeric IDs)
    return [t.strip() for t in val.split(',') if t.strip()]


# -- HTTP helpers --------------------------------------------------------------

class HTTP:
    def __init__(self, base: str, timeout: int = 120):
        self.base = base.rstrip('/')
        self.timeout = timeout

    def parse(self, sample_id: str, code: str) -> bool:
        r = httpx.post(f'{self.base}/parse', json={
            'sample_id': sample_id, 'source_code': code,
            'language': 'c', 'filename': 'snippet.c', 'overwrite': True,
        }, timeout=self.timeout)
        return r.json().get('ok', False)

    def query(self, q: str, session: str) -> str:
        r = httpx.post(f'{self.base}/query-sync', json={'query': q},
                       headers={'X-Session-Id': session}, timeout=self.timeout)
        return r.json().get('stdout', '')

    def importcpg(self, sample_id: str, session: str) -> bool:
        path = f'/workspace/cpg-out/{sample_id}'
        out = self.query(f'importCpg("{path}")', session)
        return 'Some(' in out


# -- CPG introspection ---------------------------------------------------------

@dataclass
class CPGInfo:
    sample_id: str
    cpg_path: str

    # Best method to use for tests (has real calls)
    method_full_name: str = ""
    method_name: str = ""
    method_id: str = ""         # e.g. "107374182400L"

    # A real (non-operator) call inside the best method
    call_id: str = ""
    call_code: str = ""
    called_method_full_name: str = ""

    # TypeDecl for class-based tools
    class_full_name: str = ""
    class_id: str = ""

    # All top methods for iteration
    top_methods: List[Tuple[str, str, int]] = field(default_factory=list)  # (full_name, id, n_calls)

    # A method name that actually exists inside class_full_name (for get_method_code_by_class_full_name_and_method_name)
    method_name_in_class: str = ""


def discover_cpg_info(http: HTTP, sample_id: str) -> CPGInfo:
    """Run targeted CPGQL queries to extract the richest IDs from a CPG."""
    info = CPGInfo(sample_id=sample_id, cpg_path=f'/workspace/cpg-out/{sample_id}')
    session = f'discover-{sample_id[:12]}'

    if not http.importcpg(sample_id, session):
        print("  [!] importCpg failed during discovery")
        return info

    # Top 5 real (non-operator, non-global) methods by call count
    raw = http.query(
        'cpg.method'
        '.filter(m => m.name != "<global>" && !m.name.startsWith("<operator>"))'
        '.sortBy(-_.callOut.filter(!_.methodFullName.startsWith("<operator>")).size)'
        '.take(5)'
        '.map(m => s"${m.fullName}|||${m.id}|||${m.callOut.filter(!_.methodFullName.startsWith(\"<operator>\")).size}")'
        '.l',
        session,
    )
    for entry in parse_list(raw):
        parts = entry.split('|||')
        if len(parts) == 3:
            fname, mid, n = parts[0], parts[1].strip() + 'L', int(parts[2]) if parts[2].strip().isdigit() else 0
            info.top_methods.append((fname, mid, n))

    if not info.top_methods:
        # Fall back to <global> if no named methods
        raw = http.query('cpg.method.filter(_.name == "<global>").map(m => s"${m.fullName}|||${m.id}|||0").l', session)
        for entry in parse_list(raw):
            parts = entry.split('|||')
            if len(parts) == 3:
                info.top_methods.append((parts[0], parts[1].strip() + 'L', 0))

    if info.top_methods:
        info.method_full_name, info.method_id, _ = info.top_methods[0]
        # Extract short name
        info.method_name = info.method_full_name.split('.')[-1].split(':')[0].split('(')[0]

    # Best non-operator call in the best method
    if info.method_full_name:
        raw = http.query(
            f'cpg.method.fullNameExact("{info.method_full_name}")'
            '.callOut.filter(!_.methodFullName.startsWith("<operator>"))'
            '.sortBy(_.order)'
            '.take(5)'
            '.map(c => s"${c.id}|||${c.code.take(80)}|||${c.methodFullName}")'
            '.l',
            session,
        )
        calls = parse_list(raw)
        if calls:
            parts = calls[0].split('|||')
            if len(parts) == 3:
                info.call_id = parts[0].strip() + 'L'
                info.call_code = parts[1].strip()
                info.called_method_full_name = parts[2].strip()

    # TypeDecl (for class tools - C uses file-scope typeDecls)
    raw = http.query(
        'cpg.typeDecl'
        '.filter(t => t.fullName != "<empty>" && t.name != "ANY")'
        '.sortBy(-_.method.size)'
        '.take(1)'
        '.map(t => s"${t.fullName}|||${t.id}")'
        '.l',
        session,
    )
    entries = parse_list(raw)
    if entries:
        parts = entries[0].split('|||')
        if len(parts) == 2:
            info.class_full_name = parts[0].strip()
            info.class_id = parts[1].strip() + 'L'

    # Discover a method name that actually exists inside the chosen typeDecl
    # (needed for get_method_code_by_class_full_name_and_method_name)
    if info.class_full_name:
        raw = http.query(
            f'cpg.typeDecl.filter(_.fullName == "{info.class_full_name}").method.name.take(1).l',
            session,
        )
        names = parse_list(raw)
        info.method_name_in_class = names[0] if names else '<global>'

    return info


# -- Tool definitions ----------------------------------------------------------

# (tool_name, build_args, expected_empty_reason)
# expected_empty_reason: None = should have data; string = OK to be empty
ALL_TOOLS: List[Tuple[str, Any, Optional[str]]] = [
    # Connectivity
    ("check_connection",  lambda i: {},                                         None),
    ("get_help",          lambda i: {},                                         None),
    ("ping",              lambda i: {},                                         None),

    # CPG load
    ("load_cpg",          lambda i: {"cpg_filepath": i.cpg_path},               None),

    # Method query tools
    ("get_method_callees",
     lambda i: {"method_full_name": i.method_full_name},                        None),

    ("get_method_callers",
     lambda i: {"method_full_name": i.method_full_name},
     "top-level C functions are not called within single-file CPG"),

    ("get_method_code_by_full_name",
     lambda i: {"method_full_name": i.method_full_name},                        None),

    ("get_calls_in_method_by_method_full_name",
     lambda i: {"method_full_name": i.method_full_name},                        None),

    ("get_method_full_name_by_id",
     lambda i: {"method_id": i.method_id} if i.method_id else None,            None),

    ("get_method_code_by_id",
     lambda i: {"method_id": i.method_id} if i.method_id else None,            None),

    # Call query tools
    ("get_call_code_by_id",
     lambda i: {"code_id": i.call_id} if i.call_id else None,                  None),

    ("get_method_by_call_id",
     lambda i: {"call_id": i.call_id} if i.call_id else None,                  None),

    ("get_referenced_method_full_name_by_call_id",
     lambda i: {"call_id": i.call_id} if i.call_id else None,                  None),

    # Class query tools
    ("get_class_full_name_by_id",
     lambda i: {"class_id": i.class_id} if i.class_id else None,               None),

    ("get_class_methods_by_class_full_name",
     lambda i: {"class_full_name": i.class_full_name} if i.class_full_name else None,
     None),

    ("get_method_code_by_class_full_name_and_method_name",
     lambda i: {"class_full_name": i.class_full_name,
                "method_name": i.method_name_in_class} if i.class_full_name else None,
     None),

    ("get_derived_classes_by_class_full_name",
     lambda i: {"class_full_name": i.class_full_name} if i.class_full_name else None,
     "C has no class inheritance (expected empty for .c files)"),

    ("get_parent_classes_by_class_full_name",
     lambda i: {"class_full_name": i.class_full_name} if i.class_full_name else None,
     "C has no class inheritance (expected empty for .c files)"),
]


# -- MCP result container ------------------------------------------------------

@dataclass
class ToolTestResult:
    tool: str
    status: str           # PASS / EMPTY / FAIL / SKIP / EXPECTED_EMPTY
    latency_ms: int
    args: Dict[str, Any]
    raw_output: str
    expected_empty_reason: Optional[str]
    debug_lines: List[str] = field(default_factory=list)


# -- MCP session runner --------------------------------------------------------

async def run_mcp_functional(mcp_url: str, info: CPGInfo, http: HTTP) -> List[ToolTestResult]:
    results: List[ToolTestResult] = []

    if not _MCP_AVAILABLE:
        for tool_name, _, _ in ALL_TOOLS:
            results.append(ToolTestResult(
                tool=tool_name, status='SKIP', latency_ms=0, args={},
                raw_output='', expected_empty_reason=None,
                debug_lines=['fastmcp not importable - activate mcp-joern/.venv']))
        return results

    client = MCPClient(transport=SSETransport(mcp_url))
    async with client:
        for tool_name, build_args, expected_empty_reason in ALL_TOOLS:
            # Build args - None means required ID unavailable -> SKIP
            args = build_args(info)
            if args is None:
                results.append(ToolTestResult(
                    tool=tool_name, status='SKIP', latency_ms=0, args={},
                    raw_output='',
                    expected_empty_reason=expected_empty_reason,
                    debug_lines=[f'Required ID not available in CPG for this tool']))
                continue

            t0 = time.perf_counter()
            try:
                resp = await client.call_tool(tool_name, args)
                ms = int((time.perf_counter() - t0) * 1000)
                text = resp[0].text if resp else ''
            except Exception as e:
                ms = int((time.perf_counter() - t0) * 1000)
                results.append(ToolTestResult(
                    tool=tool_name, status='FAIL', latency_ms=ms, args=args,
                    raw_output=str(e)[:400],
                    expected_empty_reason=expected_empty_reason))
                continue

            # Classify
            is_empty = not text or text.strip() in ('', '[]', 'List()')
            # Detect real errors: Java exceptions (java.lang.*), Joern REPL [Exx] errors,
            # or bare exception lines. Avoid false-positives from C identifiers like
            # "ExceptionInfo", "ErrorCode", etc. in method code/signatures.
            is_exception = bool(
                re.search(r'java\.[a-z]+\.\w*(Exception|Error)', text)   # Java exceptions
                or re.search(r'\[E\d{3}\]\s+\w.*Error:', text)            # Joern REPL errors
                or re.match(r'\s*Exception\s+in\s+thread', text)          # JVM thread errors
            )

            if is_exception and is_empty:
                status = 'FAIL'
            elif is_exception:
                # Tool ran but returned a Java exception string - treat as FAIL
                status = 'FAIL'
            elif is_empty and expected_empty_reason:
                status = 'EXPECTED_EMPTY'
            elif is_empty:
                status = 'EMPTY'
            else:
                status = 'PASS'

            tr = ToolTestResult(
                tool=tool_name, status=status, latency_ms=ms, args=args,
                raw_output=text[:600],
                expected_empty_reason=expected_empty_reason)

            # Debug diagnostics for EMPTY/FAIL tools
            if status in ('EMPTY', 'FAIL'):
                tr.debug_lines = _diagnose(tool_name, args, info, http, text)

            results.append(tr)

    return results


def _diagnose(tool: str, args: Dict, info: CPGInfo, http: HTTP, output: str) -> List[str]:
    """Run follow-up CPGQL queries to explain why a tool returned empty/failed."""
    session = f'debug-{info.sample_id[:12]}'
    lines: List[str] = []

    # Re-ensure CPG is loaded in this debug session
    http.importcpg(info.sample_id, session)

    if tool == 'get_method_callees':
        fn = args.get('method_full_name', '')
        raw = http.query(f'cpg.method.fullNameExact("{fn}").callOut.size', session)
        lines.append(f'callOut.size = {extract_repl_value(raw)}')
        raw = http.query(f'cpg.method.fullNameExact("{fn}").callOut.filter(!_.methodFullName.startsWith("<operator>")).methodFullName.take(5).l', session)
        lines.append(f'non-operator callee names: {extract_repl_value(raw)}')

    elif tool == 'get_method_callers':
        fn = args.get('method_full_name', '')
        raw = http.query(f'cpg.method.fullNameExact("{fn}").caller.size', session)
        lines.append(f'callers in CPG: {extract_repl_value(raw)}')
        raw = http.query('cpg.method.filter(_.name != "<global>").size', session)
        lines.append(f'total named methods: {extract_repl_value(raw)}')

    elif tool in ('get_method_code_by_id', 'get_method_full_name_by_id'):
        mid = args.get('method_id', '')
        lines.append(f'ID used: {mid}')
        lines.append(f'Raw output: {output[:200]}')

    elif tool in ('get_call_code_by_id', 'get_method_by_call_id',
                  'get_referenced_method_full_name_by_call_id'):
        cid = args.get('call_id', args.get('code_id', ''))
        raw = http.query('cpg.call.filter(!_.methodFullName.startsWith("<operator>")).size', session)
        lines.append(f'non-operator calls in CPG: {extract_repl_value(raw)}')
        lines.append(f'call_id used: {cid}')

    elif tool == 'get_class_full_name_by_id':
        lines.append(f'class_id used: {args.get("class_id", "")}')
        raw = http.query('cpg.typeDecl.filter(t => t.fullName != "<empty>" && t.name != "ANY").map(t => s"${t.fullName}/${t.id}").take(3).l', session)
        lines.append(f'available typeDecls: {extract_repl_value(raw)}')

    elif tool == 'get_class_methods_by_class_full_name':
        fn = args.get('class_full_name', '')
        raw = http.query(f'cpg.typeDecl.fullNameExact("{fn}").method.size', session)
        lines.append(f'methods in typeDecl: {extract_repl_value(raw)}')

    elif tool == 'get_method_code_by_class_full_name_and_method_name':
        cfn = args.get('class_full_name', '')
        mn = args.get('method_name', '')
        raw = http.query(f'cpg.typeDecl.fullNameExact("{cfn}").method.name.l', session)
        lines.append(f'all methods in class: {extract_repl_value(raw)[:200]}')
        lines.append(f'searched method_name: {mn}')

    elif tool == 'get_calls_in_method_by_method_full_name':
        fn = args.get('method_full_name', '')
        raw = http.query(f'cpg.method.fullNameExact("{fn}").callOut.size', session)
        lines.append(f'total calls (incl operators): {extract_repl_value(raw)}')
        raw = http.query(f'cpg.method.fullNameExact("{fn}").callOut.filter(!_.methodFullName.startsWith("<operator>")).size', session)
        lines.append(f'non-operator calls: {extract_repl_value(raw)}')

    elif tool == 'get_derived_classes_by_class_full_name':
        fn = args.get('class_full_name', '')
        raw = http.query(f'cpg.typeDecl.filter(_.inheritsFromTypeFullName.contains("{fn}")).fullName.l', session)
        lines.append(f'derived types: {extract_repl_value(raw)}')

    elif tool == 'get_parent_classes_by_class_full_name':
        fn = args.get('class_full_name', '')
        raw = http.query(f'cpg.typeDecl.fullNameExact("{fn}").inheritsFromTypeFullName.l', session)
        lines.append(f'parent types: {extract_repl_value(raw)}')

    if (re.search(r'java\.[a-z]+\.\w*(Exception|Error)', output)
            or re.search(r'\[E\d{3}\]\s+\w.*Error:', output)):
        lines.append(f'Server error in output: {output[:300]}')

    return lines


# -- Report --------------------------------------------------------------------

STATUS_ICON = {
    'PASS':           '✓',
    'EMPTY':          '○',
    'EXPECTED_EMPTY': '~',
    'FAIL':           '✗',
    'SKIP':           '-',
}

def print_terminal_report(info: CPGInfo, results: List[ToolTestResult]) -> None:
    w = 60
    print()
    print('=' * w)
    print('  MCP Functional Test Results')
    print('=' * w)
    print(f'  CPG      : {info.cpg_path}')
    print(f'  Method   : {info.method_full_name}')
    print(f'  MethodID : {info.method_id}')
    print(f'  CallID   : {info.call_id}')
    print(f'  CallCode : {info.call_code[:60]}')
    print(f'  Class    : {info.class_full_name}')
    print()

    by_status: Dict[str, int] = {}
    for r in results:
        by_status[r.status] = by_status.get(r.status, 0) + 1

    for r in results:
        icon = STATUS_ICON.get(r.status, '?')
        args_short = ', '.join(f'{k}={str(v)[:30]}' for k, v in r.args.items())
        print(f'  {icon} [{r.status:<14s}] {r.tool:<50s} {r.latency_ms:4d}ms')
        if r.status == 'PASS':
            preview = r.raw_output.replace('\n', ' ')[:80]
            print(f'    -> {preview}')
        elif r.status in ('EMPTY', 'FAIL'):
            for dl in r.debug_lines:
                print(f'    . {dl}')
        elif r.status == 'EXPECTED_EMPTY':
            print(f'    ~ {r.expected_empty_reason}')
        elif r.status == 'SKIP':
            reason = r.debug_lines[0] if r.debug_lines else 'no ID'
            print(f'    - {reason}')

    print()
    print('  Summary:')
    for st, icon in STATUS_ICON.items():
        cnt = by_status.get(st, 0)
        if cnt:
            print(f'    {icon} {st:<14s} {cnt}')
    print('=' * w)


def generate_markdown(info: CPGInfo, results: List[ToolTestResult], args) -> str:
    ts = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    lines: List[str] = []

    by_status: Dict[str, int] = {}
    for r in results:
        by_status[r.status] = by_status.get(r.status, 0) + 1

    pass_rate = by_status.get('PASS', 0) + by_status.get('EXPECTED_EMPTY', 0)
    total_ran = sum(by_status.get(s, 0) for s in ('PASS', 'EMPTY', 'FAIL', 'EXPECTED_EMPTY'))

    lines += [
        '# MCP Functional Test Report',
        '',
        f'**Generated:** {ts}',
        '',
        '## Test CPG',
        '',
        '| Field | Value |',
        '|-------|-------|',
        f'| Sample ID   | `{info.sample_id}` |',
        f'| CPG path    | `{info.cpg_path}` |',
        f'| Method      | `{info.method_full_name}` |',
        f'| Method ID   | `{info.method_id}` |',
        f'| Call ID     | `{info.call_id}` |',
        f'| Call code   | `{info.call_code[:80]}` |',
        f'| Called fn   | `{info.called_method_full_name}` |',
        f'| Class (typeDecl) | `{info.class_full_name}` |',
        f'| Class ID    | `{info.class_id}` |',
        '',
        '### Top Methods in CPG (by call count)',
        '',
        '| Method | ID | Calls |',
        '|--------|----|-------|',
    ]
    for fname, mid, n in info.top_methods[:8]:
        lines.append(f'| `{fname[:70]}` | `{mid}` | {n} |')
    lines.append('')

    lines += [
        '## Tool Results',
        '',
        f'**{pass_rate}/{total_ran}** tools produced correct output '
        f'({by_status.get("PASS", 0)} PASS + {by_status.get("EXPECTED_EMPTY", 0)} expected-empty)',
        '',
        '| Tool | Status | Latency | Output / Notes |',
        '|------|--------|---------|----------------|',
    ]

    for r in results:
        icon = STATUS_ICON.get(r.status, '?')
        if r.status == 'PASS':
            detail = r.raw_output.replace('\n', ' ')[:120]
        elif r.status == 'EXPECTED_EMPTY':
            detail = r.expected_empty_reason or ''
        elif r.status in ('EMPTY', 'FAIL'):
            detail = ' . '.join(r.debug_lines)[:200]
        elif r.status == 'SKIP':
            detail = r.debug_lines[0] if r.debug_lines else 'required ID not found'
        else:
            detail = r.raw_output[:120]
        lines.append(f'| `{r.tool}` | {icon} {r.status} | {r.latency_ms}ms | {detail} |')

    lines += ['']

    # Detailed debug sections for non-PASS tools
    non_pass = [r for r in results if r.status in ('EMPTY', 'FAIL') and r.debug_lines]
    if non_pass:
        lines += ['## Debug Details', '']
        for r in non_pass:
            lines += [
                f'### `{r.tool}` - {r.status}',
                '',
                f'**Args:** `{r.args}`',
                '',
            ]
            for dl in r.debug_lines:
                lines.append(f'- {dl}')
            if r.raw_output:
                lines += ['', f'**Raw output:** `{r.raw_output[:300]}`']
            lines.append('')

    lines += [
        '## Summary',
        '',
        '| Status | Count | Meaning |',
        '|--------|-------|---------|',
        f'| ✓ PASS           | {by_status.get("PASS", 0)} | Tool returned meaningful data |',
        f'| ~ EXPECTED_EMPTY | {by_status.get("EXPECTED_EMPTY", 0)} | Empty is correct (e.g. no inheritance in C) |',
        f'| ○ EMPTY          | {by_status.get("EMPTY", 0)} | Tool ran but returned nothing - see debug |',
        f'| ✗ FAIL           | {by_status.get("FAIL", 0)} | Exception / server error |',
        f'| - SKIP           | {by_status.get("SKIP", 0)} | Required node ID not available |',
        '',
    ]

    return '\n'.join(lines)


# -- Sample selection ----------------------------------------------------------

def select_rich_sample(http: HTTP, sven_path: str, n_candidates: int = 30) -> Optional[Tuple[str, str]]:
    """Parse up to n_candidates SVEN C samples; return (sample_id, code) of richest."""
    print(f'Scanning up to {n_candidates} SVEN C samples for richest CPG...')
    best_score, best_id, best_code = -1, None, None

    with open(sven_path) as f:
        count = 0
        for line in f:
            d = json.loads(line)
            if d.get('language') != 'c':
                continue
            sid, code = d['id'], d['code']
            ok = http.parse(sid, code)
            if not ok:
                continue
            sess = f'sel-{sid[:8]}'
            http.importcpg(sid, sess)
            methods = parse_int(http.query(
                'cpg.method.filter(m => m.name != "<global>" && !m.name.startsWith("<operator>")).size', sess))
            calls = parse_int(http.query(
                'cpg.call.filter(!_.methodFullName.startsWith("<operator>")).size', sess))
            score = methods * calls
            print(f'  {sid[:16]}  methods={methods:3d}  calls={calls:3d}  score={score}')
            if score > best_score:
                best_score, best_id, best_code = score, sid, code
            count += 1
            if count >= n_candidates:
                break

    if best_id:
        print(f'\nSelected: {best_id}  (score={best_score})')
    return (best_id, best_code) if best_id else None


# -- Main ----------------------------------------------------------------------

def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--http-url',  default='http://localhost:8080')
    ap.add_argument('--mcp-url',   default='http://localhost:9000/sse')
    ap.add_argument('--sven',
                    default='/datadrive/data/raw/sven/sven_preprocessed_v2.jsonl')
    ap.add_argument('--sample-id', default='',
                    help='Skip selection and use this sample ID directly')
    ap.add_argument('--candidates', type=int, default=30,
                    help='How many samples to scan during selection (default: 30)')
    ap.add_argument('--report',    default='mcp_functional_report.md')
    args = ap.parse_args()

    if not _MCP_AVAILABLE:
        print('ERROR: fastmcp not importable. Activate mcp-joern/.venv first.')
        sys.exit(1)

    http = HTTP(args.http_url, timeout=180)

    # -- Select / load sample --------------------------------------------------
    if args.sample_id:
        # User specified an ID - look up code from SVEN
        code = None
        with open(args.sven) as f:
            for line in f:
                d = json.loads(line)
                if d['id'] == args.sample_id:
                    code = d['code']
                    break
        if code is None:
            print(f'ERROR: sample_id {args.sample_id} not found in {args.sven}')
            sys.exit(1)
        sample_id = args.sample_id
        print(f'Using specified sample: {sample_id}')
        if not http.parse(sample_id, code):
            print('WARNING: parse returned not-ok (CPG may already exist; continuing)')
    else:
        result = select_rich_sample(http, args.sven, args.candidates)
        if result is None:
            print('ERROR: could not parse any SVEN C sample')
            sys.exit(1)
        sample_id, code = result

    # -- Discover CPG IDs ------------------------------------------------------
    print(f'\nDiscovering CPG node IDs for {sample_id}...')
    info = discover_cpg_info(http, sample_id)
    print(f'  method      : {info.method_full_name}')
    print(f'  method_id   : {info.method_id}')
    print(f'  call_id     : {info.call_id}')
    print(f'  call_code   : {info.call_code[:60]}')
    print(f'  class_fn    : {info.class_full_name}')
    print(f'  class_id    : {info.class_id}')
    print(f'  top methods : {[m[0][:40] for m in info.top_methods[:3]]}')

    if not info.method_full_name:
        print('ERROR: could not discover any method in CPG - check parse output')
        sys.exit(1)

    # -- Run MCP functional tests ----------------------------------------------
    print(f'\nRunning {len(ALL_TOOLS)} MCP tool tests via {args.mcp_url} ...')
    results = asyncio.run(run_mcp_functional(args.mcp_url, info, http))

    # -- Report ----------------------------------------------------------------
    print_terminal_report(info, results)

    md = generate_markdown(info, results, args)
    Path(args.report).write_text(md, encoding='utf-8')
    print(f'\nMarkdown report: {Path(args.report).resolve()}')

    # Non-zero exit if any tool FAIL or unexpected EMPTY
    failures = sum(1 for r in results if r.status in ('FAIL', 'EMPTY'))
    if failures:
        sys.exit(1)


if __name__ == '__main__':
    main()
