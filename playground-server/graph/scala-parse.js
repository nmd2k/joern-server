/** Extract individual tuple strings from Scala `List(...)` output. */
export function extractScalaTuples(stdout) {
  if (!stdout) return [];
  let idx = stdout.indexOf('= List(');
  let contentStart;
  if (idx !== -1) {
    contentStart = idx + 7;
  } else {
    idx = stdout.indexOf('List(');
    if (idx === -1) return [];
    contentStart = idx + 5;
  }
  let depth = 1;
  let pos = contentStart;
  while (pos < stdout.length && depth > 0) {
    if (stdout[pos] === '(') depth += 1;
    else if (stdout[pos] === ')') depth -= 1;
    pos += 1;
  }
  const content = stdout.slice(contentStart, pos - 1);
  const tuples = [];
  let i = 0;
  while (i < content.length) {
    if (content[i] === '(') {
      let d = 1;
      let j = i + 1;
      while (j < content.length && d > 0) {
        if (content[j] === '(') d += 1;
        else if (content[j] === ')') d -= 1;
        j += 1;
      }
      if (d === 0) {
        tuples.push(content.slice(i, j));
        i = j;
        continue;
      }
    }
    i += 1;
  }
  return tuples;
}

/** Split a Scala tuple string by top-level commas into fields. */
export function splitScalaTuple(tupleStr) {
  let inner = tupleStr;
  if (inner.startsWith('(') && inner.endsWith(')')) inner = inner.slice(1, -1);
  const fields = [];
  let current = '';
  let depth = 0;
  let inString = false;
  let escaped = false;
  for (let i = 0; i < inner.length; i++) {
    const ch = inner[i];
    if (escaped) {
      current += ch;
      escaped = false;
      continue;
    }
    if (ch === '\\') {
      current += ch;
      escaped = true;
      continue;
    }
    if (ch === '"') {
      inString = !inString;
      current += ch;
      continue;
    }
    if (inString) {
      current += ch;
      continue;
    }
    if ('([{'.includes(ch)) {
      depth += 1;
      current += ch;
      continue;
    }
    if (')]}'.includes(ch)) {
      depth -= 1;
      current += ch;
      continue;
    }
    if (ch === ',' && depth === 0) {
      fields.push(current.trim());
      current = '';
      continue;
    }
    current += ch;
  }
  if (current) fields.push(current.trim());
  return fields;
}

/** Parse a single Scala value into JS. */
export function parseScalaField(field) {
  let f = field.trim();
  if (!f) return null;
  if (f.endsWith('L') && f.length > 1) {
    const rest = f.slice(0, -1);
    if (/^-?\d+$/.test(rest)) f = rest;
  }
  if (f.startsWith('"') && f.endsWith('"') && f.length >= 2) {
    return f.slice(1, -1).replace(/\\"/g, '"').replace(/\\\\/g, '\\');
  }
  if (f === 'None') return null;
  if (f.startsWith('Some(') && f.endsWith(')')) {
    const innerVal = f.slice(5, -1).trim();
    const eqIdx = innerVal.indexOf(' = ');
    if (eqIdx !== -1) {
      const numStr = innerVal.slice(eqIdx + 3).trim();
      const n = Number(numStr);
      return Number.isNaN(n) ? numStr : n;
    }
    const n = Number(innerVal);
    return Number.isNaN(n) ? innerVal : n;
  }
  const n = Number(f);
  return Number.isNaN(n) ? f : n;
}

/** Parse 6-field metadata tuples into { nodeId: metadata }. */
export function parseMetadataTuples(stdout) {
  const metadata = {};
  for (const t of extractScalaTuples(stdout)) {
    const fields = splitScalaTuple(t);
    if (fields.length < 6) continue;
    try {
      const fid = parseScalaField(fields[0]);
      const code = parseScalaField(fields[1]);
      const lineNum = parseScalaField(fields[2]);
      const colNum = parseScalaField(fields[3]);
      const order = parseScalaField(fields[4]);
      const nodeType = parseScalaField(fields[5]);
      metadata[String(fid)] = {
        code: typeof code === 'string' ? code : code != null ? String(code) : '',
        line_number: lineNum,
        column_number: colNum,
        order: order != null ? order : -1,
        argument_index: -1,
        node_type: typeof nodeType === 'string' ? nodeType : '',
      };
    } catch {
      continue;
    }
  }
  return metadata;
}

/** Parse 7-field AST tuples into (nodes, edges, metadata). */
export function parseAstTuples(stdout) {
  const nodes = [];
  const edges = [];
  const metadata = {};
  for (const t of extractScalaTuples(stdout)) {
    const fields = splitScalaTuple(t);
    if (fields.length < 7) continue;
    try {
      const fid = parseScalaField(fields[0]);
      const code = parseScalaField(fields[1]);
      const lineNum = parseScalaField(fields[2]);
      const colNum = parseScalaField(fields[3]);
      const order = parseScalaField(fields[4]);
      const nodeType = parseScalaField(fields[5]);
      const parentId = parseScalaField(fields[6]);
      const nodeIdStr = String(fid);
      nodes.push({
        id: nodeIdStr,
        label: typeof nodeType === 'string' ? nodeType : nodeType != null ? String(nodeType) : '',
      });
      metadata[nodeIdStr] = {
        code: typeof code === 'string' ? code : code != null ? String(code) : '',
        line_number: lineNum,
        column_number: colNum,
        order: order != null ? order : -1,
        argument_index: -1,
        node_type: typeof nodeType === 'string' ? nodeType : '',
      };
      if (parentId != null) {
        edges.push({ source: String(parentId), target: nodeIdStr, label: '' });
      }
    } catch {
      continue;
    }
  }

  const nodeIds = new Set(nodes.map((n) => n.id));
  for (const e of edges) {
    for (const nid of [e.source, e.target]) {
      if (!nodeIds.has(nid)) {
        nodes.push({ id: nid, label: '', shape: '' });
        metadata[nid] = {};
        nodeIds.add(nid);
      }
    }
  }

  return { nodes, edges, metadata };
}
