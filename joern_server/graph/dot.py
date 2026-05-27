import re


def extract_dot_from_stdout(stdout: str) -> str:
    """Pull DOT text from Joern REPL output (may be wrapped in List(\"\"\"...\"\"\"))."""
    if not stdout:
        return ""
    m = re.search(r'"""(digraph[\s\S]*?)"""', stdout)
    if m:
        return m.group(1).strip()
    idx = stdout.find("digraph")
    if idx >= 0:
        return stdout[idx:].strip()
    return stdout.strip()


def dot_to_graph(dot_text: str) -> dict:
    """Parse Joern DOT output into {nodes, edges} JSON structure."""
    nodes: list[dict[str, str]] = []
    edges: list[dict[str, str]] = []
    text = extract_dot_from_stdout(dot_text)
    if not text:
        return {"nodes": nodes, "edges": edges}

    m = re.match(r'digraph\s+"([^"]*)"\s*\{', text)
    if not m:
        return {"nodes": nodes, "edges": edges}

    content_start = m.end()
    depth = 1
    content_end = content_start
    for i, ch in enumerate(text[content_start:], start=content_start):
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                content_end = i
                break

    content = text[content_start:content_end].strip()
    if not content:
        return {"nodes": nodes, "edges": edges}

    for line in content.split("\n"):
        line = line.strip()
        if not line:
            continue
        if line.endswith(";"):
            line = line[:-1].strip()
        if not line or line.startswith("node ["):
            continue

        edge_m = re.match(r'"([^"]*)"\s*->\s*"([^"]*)"(?:\s*\[([^\]]*)\])?', line)
        if edge_m:
            attrs_str = edge_m.group(3) or ""
            label_m = re.search(r'label\s*=\s*"([^"]*)"', attrs_str)
            edges.append({
                "source": edge_m.group(1),
                "target": edge_m.group(2),
                "label": label_m.group(1) if label_m else "",
            })
            continue

        node_m = re.match(r'"([^"]*)"\s*\[', line)
        if node_m:
            label_m = re.search(r'label\s*=\s*<(.*)>\s*\]\s*$', line)
            if not label_m:
                label_m = re.search(r'label\s*=\s*"([^"]*)"', line)
            shape_m = re.search(r'shape\s*=\s*"([^"]*)"', line)
            nodes.append({
                "id": node_m.group(1),
                "label": label_m.group(1) if label_m else "",
                "shape": shape_m.group(1) if shape_m else "",
            })

    declared_ids = {n["id"] for n in nodes}
    for e in edges:
        for nid in (e["source"], e["target"]):
            if nid not in declared_ids:
                nodes.append({"id": nid, "label": "", "shape": ""})
                declared_ids.add(nid)

    return {"nodes": nodes, "edges": edges}
