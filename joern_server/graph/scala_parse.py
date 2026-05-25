def _extract_scala_tuples(stdout: str) -> list[str]:
    """Extract individual tuple strings from Scala `List(...)` output."""
    if not stdout:
        return []
    # Find "= List(" — the value list, skipping the type-annotation List(...)
    idx = stdout.find("= List(")
    if idx != -1:
        content_start = idx + 7
    else:
        idx = stdout.find("List(")
        if idx == -1:
            return []
        content_start = idx + 5
    depth = 1
    pos = content_start
    while pos < len(stdout) and depth > 0:
        if stdout[pos] == "(":
            depth += 1
        elif stdout[pos] == ")":
            depth -= 1
        pos += 1
    content = stdout[content_start:pos - 1]
    tuples: list[str] = []
    i = 0
    while i < len(content):
        if content[i] == "(":
            d = 1
            j = i + 1
            while j < len(content) and d > 0:
                if content[j] == "(":
                    d += 1
                elif content[j] == ")":
                    d -= 1
                j += 1
            if d == 0:
                tuples.append(content[i:j])
                i = j
                continue
        i += 1
    return tuples


def _split_scala_tuple(tuple_str: str) -> list[str]:
    """Split a Scala tuple string by top-level commas into fields."""
    inner = tuple_str[1:-1] if tuple_str.startswith("(") and tuple_str.endswith(")") else tuple_str
    fields: list[str] = []
    current: list[str] = []
    depth = 0
    in_string = False
    escaped = False
    i = 0
    while i < len(inner):
        ch = inner[i]
        if escaped:
            current.append(ch)
            escaped = False
            i += 1
            continue
        if ch == "\\":
            current.append(ch)
            escaped = True
            i += 1
            continue
        if ch == '"':
            in_string = not in_string
            current.append(ch)
            i += 1
            continue
        if in_string:
            current.append(ch)
            i += 1
            continue
        if ch in "([{":
            depth += 1
            current.append(ch)
            i += 1
            continue
        if ch in ")]}":
            depth -= 1
            current.append(ch)
            i += 1
            continue
        if ch == "," and depth == 0:
            fields.append("".join(current).strip())
            current = []
            i += 1
            continue
        current.append(ch)
        i += 1
    if current:
        fields.append("".join(current).strip())
    return fields


def _parse_scala_field(field: str):
    """Parse a single Scala value (string, int, Some(value=...), None) into Python."""
    field = field.strip()
    if not field:
        return None
    # Strip trailing "L" suffix from Scala Long literals
    long_suffix = False
    if field.endswith("L") and len(field) > 1:
        rest = field[:-1]
        if rest.isdigit() or (rest.startswith("-") and rest[1:].isdigit()):
            field = rest
            long_suffix = True
    if field.startswith('"') and field.endswith('"') and len(field) >= 2:
        inner = field[1:-1]
        return inner.replace('\\"', '"').replace("\\\\", "\\")
    if field == "None":
        return None
    if field.startswith("Some(") and field.endswith(")"):
        inner_val = field[5:-1].strip()
        # Handle `Some(value = 42)` format from Option[Int] output
        eq_idx = inner_val.find(" = ")
        if eq_idx != -1:
            num_str = inner_val[eq_idx + 3:].strip()
            try:
                return int(num_str)
            except ValueError:
                return inner_val
        try:
            return int(inner_val)
        except ValueError:
            return inner_val
    try:
        return int(field)
    except ValueError:
        return field


def _parse_metadata_tuples(stdout: str) -> dict[str, dict]:
    """Parse 6-field metadata tuples (id, code, line, column, order, label) into {nodeId: metadata}."""
    metadata: dict[str, dict] = {}
    for t in _extract_scala_tuples(stdout):
        fields = _split_scala_tuple(t)
        if len(fields) < 6:
            continue
        try:
            fid = _parse_scala_field(fields[0])
            code = _parse_scala_field(fields[1])
            line_num = _parse_scala_field(fields[2])
            col_num = _parse_scala_field(fields[3])
            order = _parse_scala_field(fields[4])
            node_type = _parse_scala_field(fields[5])
            metadata[str(fid)] = {
                "code": code if isinstance(code, str) else str(code) if code is not None else "",
                "line_number": line_num,
                "column_number": col_num,
                "order": order if order is not None else -1,
                "argument_index": -1,
                "node_type": node_type if isinstance(node_type, str) else "",
            }
        except Exception:
            continue
    return metadata


def _parse_ast_tuples(stdout: str) -> tuple[list[dict], list[dict], dict[str, dict]]:
    """Parse 7-field AST tuples (id, code, line, column, order, label, parentId) into (nodes, edges, metadata)."""
    nodes: list[dict] = []
    edges: list[dict] = []
    metadata: dict[str, dict] = {}
    for t in _extract_scala_tuples(stdout):
        fields = _split_scala_tuple(t)
        if len(fields) < 7:
            continue
        try:
            fid = _parse_scala_field(fields[0])
            code = _parse_scala_field(fields[1])
            line_num = _parse_scala_field(fields[2])
            col_num = _parse_scala_field(fields[3])
            order = _parse_scala_field(fields[4])
            node_type = _parse_scala_field(fields[5])
            parent_id = _parse_scala_field(fields[6])
            node_id_str = str(fid)
            nodes.append({
                "id": node_id_str,
                "label": node_type if isinstance(node_type, str) else str(node_type) if node_type is not None else "",
            })
            metadata[node_id_str] = {
                "code": code if isinstance(code, str) else str(code) if code is not None else "",
                "line_number": line_num,
                "column_number": col_num,
                "order": order if order is not None else -1,
                "argument_index": -1,
                "node_type": node_type if isinstance(node_type, str) else "",
            }
            if parent_id is not None:
                edges.append({
                    "source": str(parent_id),
                    "target": node_id_str,
                    "label": "",
                })
        except Exception:
            continue

    node_ids = {n["id"] for n in nodes}
    for e in edges:
        for nid in (e["source"], e["target"]):
            if nid not in node_ids:
                nodes.append({"id": nid, "label": "", "shape": ""})
                metadata[nid] = {}
                node_ids.add(nid)

    return nodes, edges, metadata
