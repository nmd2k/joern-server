/** Pull DOT text from Joern REPL output (may be wrapped in List("""...""")). */
export function extractDotFromStdout(stdout) {
  const dots = extractAllDotsFromStdout(stdout);
  if (!dots.length) return '';
  if (dots.length === 1) return dots[0];
  return mergeDotGraphs(dots);
}

/** Extract one or more DOT digraph strings from Joern REPL stdout. */
export function extractAllDotsFromStdout(stdout) {
  if (!stdout) return [];
  const dots = [];
  const re = /"""(digraph[\s\S]*?)"""/g;
  let m;
  while ((m = re.exec(stdout)) !== null) {
    const text = m[1].trim();
    if (text) dots.push(text);
  }
  if (dots.length) return dots;
  const idx = stdout.indexOf('digraph');
  if (idx >= 0) return [stdout.slice(idx).trim()];
  return [];
}

/** Merge multiple DOT digraphs into a single digraph string. */
export function mergeDotGraphs(dotTexts) {
  const mergedNodes = [];
  const mergedEdges = [];
  const seenNodeIds = new Set();
  const seenEdgeKeys = new Set();

  for (const dotText of dotTexts) {
    const graph = dotToGraph(dotText);
    for (const node of graph.nodes) {
      if (seenNodeIds.has(node.id)) continue;
      mergedNodes.push(node);
      seenNodeIds.add(node.id);
    }
    for (const edge of graph.edges) {
      const key = `${edge.source}\0${edge.target}\0${edge.label || ''}`;
      if (seenEdgeKeys.has(key)) continue;
      mergedEdges.push(edge);
      seenEdgeKeys.add(key);
    }
  }

  if (!mergedNodes.length && !mergedEdges.length) return '';

  const lines = ['digraph "merged" {'];
  for (const node of mergedNodes) {
    const label = (node.label || '').replace(/"/g, '\\"');
    const shape = node.shape || 'box';
    lines.push(`  "${node.id}" [label="${label}" shape="${shape}"];`);
  }
  for (const edge of mergedEdges) {
    const label = edge.label || '';
    if (label) {
      const safeLabel = label.replace(/"/g, '\\"');
      lines.push(`  "${edge.source}" -> "${edge.target}" [label="${safeLabel}"];`);
    } else {
      lines.push(`  "${edge.source}" -> "${edge.target}";`);
    }
  }
  lines.push('}');
  return lines.join('\n');
}

/** Parse Joern DOT output into { nodes, edges } JSON structure. */
export function dotToGraph(dotText) {
  const nodes = [];
  const edges = [];
  const text = extractDotFromStdout(dotText);
  if (!text) return { nodes, edges };

  const m = text.match(/^digraph\s+"([^"]*)"\s*\{/);
  if (!m) return { nodes, edges };

  const contentStart = m.index + m[0].length;
  let depth = 1;
  let contentEnd = contentStart;
  for (let i = contentStart; i < text.length; i++) {
    const ch = text[i];
    if (ch === '{') depth += 1;
    else if (ch === '}') {
      depth -= 1;
      if (depth === 0) {
        contentEnd = i;
        break;
      }
    }
  }

  const content = text.slice(contentStart, contentEnd).trim();
  if (!content) return { nodes, edges };

  for (const rawLine of content.split('\n')) {
    let line = rawLine.trim();
    if (!line) continue;
    if (line.endsWith(';')) line = line.slice(0, -1).trim();
    if (!line || line.startsWith('node [')) continue;

    const edgeM = line.match(/^"([^"]*)"\s*->\s*"([^"]*)"(?:\s*\[([^\]]*)\])?/);
    if (edgeM) {
      const attrsStr = edgeM[3] || '';
      const labelM = attrsStr.match(/label\s*=\s*"([^"]*)"/);
      edges.push({
        source: edgeM[1],
        target: edgeM[2],
        label: labelM ? labelM[1] : '',
      });
      continue;
    }

    const nodeM = line.match(/^"([^"]*)"\s*\[/);
    if (nodeM) {
      let labelM = line.match(/label\s*=\s*<(.*)>\s*\]\s*$/);
      if (!labelM) labelM = line.match(/label\s*=\s*"([^"]*)"/);
      const shapeM = line.match(/shape\s*=\s*"([^"]*)"/);
      nodes.push({
        id: nodeM[1],
        label: labelM ? labelM[1] : '',
        shape: shapeM ? shapeM[1] : '',
      });
    }
  }

  const declaredIds = new Set(nodes.map((n) => n.id));
  for (const e of edges) {
    for (const nid of [e.source, e.target]) {
      if (!declaredIds.has(nid)) {
        nodes.push({ id: nid, label: '', shape: '' });
        declaredIds.add(nid);
      }
    }
  }

  return { nodes, edges };
}
