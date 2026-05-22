import re


def dot_to_graph(dot_text: str) -> dict:
    """Parse Joern DOT output into {nodes, edges} JSON structure."""
    nodes: list[dict[str, str]] = []
    edges: list[dict[str, str]] = []
    if not dot_text:
        return {"nodes": nodes, "edges": edges}

    text = dot_text.strip()
    idx = text.find("digraph")
    if idx == -1:
        return {"nodes": nodes, "edges": edges}
    text = text[idx:]

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
        if not line:
            continue

        edge_m = re.match(r'"([^"]*)"\s*->\s*"([^"]*)"(?:\s*\[([^\]]*)\])?', line)
        if edge_m:
            attrs_str = edge_m.group(3) or ""
            label_m = re.search(r'label="([^"]*)"', attrs_str)
            edges.append({
                "source": edge_m.group(1),
                "target": edge_m.group(2),
                "label": label_m.group(1) if label_m else "",
            })
            continue

        node_m = re.match(r'"([^"]*)"(?:\s*\[([^\]]*)\])?', line)
        if node_m:
            attrs_str = node_m.group(2) or ""
            label_m = re.search(r'label="([^"]*)"', attrs_str)
            shape_m = re.search(r'shape="([^"]*)"', attrs_str)
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
