export function escapeMethodFullName(methodFullName) {
  return methodFullName.replace(/\\/g, '\\\\').replace(/"/g, '\\"');
}

export function escapeFileName(fileName) {
  return fileName.replace(/\\/g, '\\\\').replace(/"/g, '\\"');
}

function internalMethodsFilter() {
  return (
    '.filterNot(_.fullName.contains("unresolved"))' +
    '.filterNot(_.fullName.contains("<operator>"))' +
    '.filterNot(_.isExternal)'
  );
}

/** Match methods by filename (exact or path suffix). Avoid regex — Scala string escapes break `.*foo\\.c$`. */
export function fileScopeTraversal(fileName) {
  const internal = internalMethodsFilter();
  const normalized = fileName.replace(/\\/g, '/').trim();
  if (!normalized) {
    return `cpg.method${internal}.take(0)`;
  }
  const basename = normalized.includes('/') ? normalized.split('/').pop() : normalized;
  const escapedFull = escapeFileName(normalized);
  const escapedBase = escapeFileName(basename);

  if (basename && basename !== normalized) {
    return (
      `cpg.method.filter(m => m.filename.endsWith("${escapedBase}")` +
      ` || m.filename.endsWith("${escapedFull}")` +
      ` || m.filename == "${escapedFull}")${internal}`
    );
  }
  return `cpg.method.filename("${escapedBase}")${internal}`;
}

/** Export DOT for each method matched by the traversal. */
export function dotExportQuery(traversal, dotStep) {
  return `${traversal}.${dotStep}.l`;
}

export function parseScope(data) {
  let scope = String(data.scope || 'method').trim().toLowerCase();
  if (!['method', 'file', 'cpg'].includes(scope)) scope = 'method';
  let nodeLimit = Number.parseInt(String(data.node_limit ?? 500), 10);
  if (Number.isNaN(nodeLimit)) nodeLimit = 500;
  nodeLimit = Math.max(1, Math.min(nodeLimit, 5000));
  return { scope, nodeLimit };
}

export function methodTraversal(scope, { methodFullName, fileName, nodeLimit }) {
  const internal = internalMethodsFilter();
  if (scope === 'method') {
    if (!methodFullName) {
      return { traversal: null, err: { status: 400, body: { error: 'missing required field: method_full_name', code: 'bad_request' } } };
    }
    const escaped = escapeMethodFullName(methodFullName);
    return { traversal: `cpg.method.fullNameExact("${escaped}")`, err: null };
  }
  if (scope === 'file') {
    if (!fileName) {
      return { traversal: null, err: { status: 400, body: { error: 'missing required field: file_name', code: 'bad_request' } } };
    }
    return { traversal: fileScopeTraversal(fileName), err: null };
  }
  return { traversal: `cpg.method${internal}.take(${nodeLimit})`, err: null };
}

export function resolveGraphScope(data) {
  const methodFullName = String(data.method_full_name || '').trim();
  const fileName = String(data.file_name || '').trim();
  const { scope, nodeLimit } = parseScope(data);
  const { traversal, err } = methodTraversal(scope, { methodFullName, fileName, nodeLimit });
  if (err) {
    return { scope, methodFullName, fileName, nodeLimit, traversal: '', err };
  }
  return { scope, methodFullName, fileName, nodeLimit, traversal, err: null };
}
