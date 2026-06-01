import { jsonError } from './errors.js';
import {
  dotToGraph,
  extractAllDotsFromStdout,
  mergeDotGraphs,
} from './dot.js';
import {
  dotExportQuery,
  escapeMethodFullName,
  resolveGraphScope,
} from './queries.js';
import { fetchNodeMetadata } from './metadata.js';
import { parseAstTuples } from './scala-parse.js';
import {
  joernStdoutHasError,
  parseUpstreamSuccess,
  postQuerySync,
} from './joern-client.js';

function isStubCfg(graph) {
  const nodes = graph.nodes || [];
  if (nodes.length > 3) return false;
  const labels = nodes.map((n) => String(n.label || '').toUpperCase());
  if (!labels.length) return true;
  const nonTrivial = labels.filter(
    (lab) => lab && !lab.includes('METHOD') && !lab.includes('RETURN') && !['RET', '<EMPTY>', ''].includes(lab),
  );
  return nonTrivial.length === 0 && nodes.length <= 3;
}

function mapQueryFailed(err, label, graphKind) {
  if (err.status === 422 && err.body?.code === 'query_failed') {
    return {
      status: 422,
      body: jsonError(`${graphKind} query failed for method: ${label}`, { code: 'query_failed' }),
    };
  }
  return err;
}

/**
 * @param {string} joernUrl
 * @param {string} query
 * @param {Record<string, string>} headers
 * @param {(url: string, query: string, headers: Record<string, string>) => Promise<{status: number, data: object}>} queryFn
 */
async function fetchDot(joernUrl, query, headers, queryFn) {
  try {
    const { status, data } = await queryFn(joernUrl, query, headers);
    if (status !== 200) {
      return { stdout: null, err: { status, body: data } };
    }
    if (!parseUpstreamSuccess(data)) {
      return { stdout: null, err: { status: 422, body: jsonError('query failed', { code: 'query_failed' }) } };
    }
    const rawStdout = data.stdout || '';
    if (joernStdoutHasError(rawStdout)) {
      const detail = rawStdout.trim().split('\n', 1)[0].slice(0, 300);
      return {
        stdout: null,
        err: { status: 422, body: jsonError(`Joern query error: ${detail}`, { code: 'query_failed' }) },
      };
    }
    return { stdout: rawStdout, err: null };
  } catch (err) {
    if (err.code === 'query_timeout') {
      return { stdout: null, err: { status: 504, body: jsonError('query timed out', { code: 'query_timeout' }) } };
    }
    return { stdout: null, err: { status: 502, body: jsonError(String(err.message || err), { code: 'joern_error' }) } };
  }
}

async function dotToGraphResponse(stdout, methodFullName, {
  joernUrl,
  headers,
  emptyMessage,
  skipStubCheck = false,
  metadataLimit = 250,
  queryFn,
}) {
  const dotTexts = extractAllDotsFromStdout(stdout);
  let graph;
  if (dotTexts.length > 1) {
    graph = dotToGraph(mergeDotGraphs(dotTexts));
  } else {
    graph = dotToGraph(stdout);
  }

  if (!graph.nodes.length && !graph.edges.length) {
    return { body: null, err: { status: 422, body: jsonError(emptyMessage, { code: 'empty_result' }) } };
  }
  if (!skipStubCheck && isStubCfg(graph)) {
    return {
      body: null,
      err: {
        status: 422,
        body: jsonError(
          'Method has no analyzable control flow (unresolved stub). Pick a method from project source.',
          { code: 'stub_method' },
        ),
      },
    };
  }

  const responseBody = {
    nodes: graph.nodes,
    edges: graph.edges,
    method_full_name: methodFullName,
  };
  const nodeIds = graph.nodes.map((n) => n.id);
  if (nodeIds.length <= metadataLimit) {
    try {
      responseBody.metadata = await fetchNodeMetadata(joernUrl, nodeIds, headers, queryFn);
    } catch {
      responseBody.metadata = {};
    }
  } else {
    responseBody.metadata = {};
  }
  return { body: responseBody, err: null };
}

export function createGraphHandlers({ joernUrl, queryFn = postQuerySync } = {}) {
  if (!joernUrl) {
    throw new Error('joernUrl is required');
  }

  async function handleCfg(data, headers) {
    const { scope, methodFullName, fileName, nodeLimit, traversal, err: scopeErr } = resolveGraphScope(data);
    if (scopeErr) return scopeErr;

    const query = dotExportQuery(traversal, 'dotCfg');
    const { stdout, err } = await fetchDot(joernUrl, query, headers, queryFn);
    if (err) {
      return mapQueryFailed(err, methodFullName || fileName, 'CFG');
    }

    const label = methodFullName || fileName || `cpg(limit=${nodeLimit})`;
    const { body, err: graphErr } = await dotToGraphResponse(stdout || '', label, {
      joernUrl,
      headers,
      emptyMessage: `No CFG found for scope=${scope}`,
      skipStubCheck: scope !== 'method',
      queryFn,
    });
    if (graphErr) return graphErr;
    body.scope = scope;
    if (fileName) body.file_name = fileName;
    return { status: 200, body };
  }

  async function handlePdg(data, headers) {
    const { scope, methodFullName, fileName, nodeLimit, traversal, err: scopeErr } = resolveGraphScope(data);
    if (scopeErr) return scopeErr;

    const query = dotExportQuery(traversal, 'dotPdg');
    const { stdout, err } = await fetchDot(joernUrl, query, headers, queryFn);
    if (err) {
      return mapQueryFailed(err, methodFullName || fileName, 'PDG');
    }

    const label = methodFullName || fileName || `cpg(limit=${nodeLimit})`;
    const { body, err: graphErr } = await dotToGraphResponse(stdout || '', label, {
      joernUrl,
      headers,
      emptyMessage: `No PDG found for scope=${scope}`,
      skipStubCheck: true,
      queryFn,
    });
    if (graphErr) return graphErr;
    body.scope = scope;
    if (fileName) body.file_name = fileName;
    return { status: 200, body };
  }

  async function handleDfg(data, headers) {
    const { scope, methodFullName, fileName, nodeLimit, traversal, err: scopeErr } = resolveGraphScope(data);
    if (scopeErr) return scopeErr;

    const sourcePattern = String(data.source_pattern || '').trim();
    const sinkPattern = String(data.sink_pattern || '').trim();

    let query;
    if (sourcePattern && sinkPattern) {
      if (scope !== 'method' || !methodFullName) {
        return {
          status: 400,
          body: jsonError('source_pattern/sink_pattern flows require scope=method and method_full_name'),
        };
      }
      const escaped = escapeMethodFullName(methodFullName);
      const escapedSource = sourcePattern.replace(/\\/g, '\\\\').replace(/"/g, '\\"');
      const escapedSink = sinkPattern.replace(/\\/g, '\\\\').replace(/"/g, '\\"');
      query =
        `cpg.method.fullNameExact("${escaped}")` +
        `.reachableByFlows(cpg.code("${escapedSource}").l, cpg.code("${escapedSink}").l).p`;
    } else {
      query = dotExportQuery(traversal, 'dotDdg');
    }

    const { stdout, err } = await fetchDot(joernUrl, query, headers, queryFn);
    if (err) {
      return mapQueryFailed(err, methodFullName || fileName, 'DFG');
    }

    if (sourcePattern && sinkPattern) {
      return {
        status: 200,
        body: {
          flows_raw: stdout || '',
          method_full_name: methodFullName,
          source_pattern: sourcePattern,
          sink_pattern: sinkPattern,
        },
      };
    }

    const label = methodFullName || fileName || `cpg(limit=${nodeLimit})`;
    const { body, err: graphErr } = await dotToGraphResponse(stdout || '', label, {
      joernUrl,
      headers,
      emptyMessage: `No DFG found for scope=${scope} (no internal methods or empty dependence graph)`,
      skipStubCheck: true,
      queryFn,
    });
    if (graphErr) return graphErr;
    body.scope = scope;
    if (fileName) body.file_name = fileName;
    return { status: 200, body };
  }

  async function handleAst(data, headers) {
    const { scope, methodFullName, fileName, nodeLimit, traversal, err: scopeErr } = resolveGraphScope(data);
    if (scopeErr) return scopeErr;

    const astLimit = scope === 'cpg' ? nodeLimit : Math.max(nodeLimit, 5000);
    const query =
      `${traversal}.ast.take(${astLimit}).map(node => ` +
      `(node.id, node.code, node.lineNumber, node.columnNumber, ` +
      `node.order, node.label, ` +
      `node.astParent.id)` +
      `).l`;

    try {
      const { status, data: respData } = await queryFn(joernUrl, query, headers);
      if (status !== 200) {
        return { status, body: respData };
      }
      if (!parseUpstreamSuccess(respData)) {
        return {
          status: 422,
          body: jsonError(`AST query failed for scope=${scope}`, { code: 'query_failed' }),
        };
      }
      const stdout = respData.stdout || '';
      if (joernStdoutHasError(stdout)) {
        const detail = stdout.trim().split('\n', 1)[0].slice(0, 300);
        return {
          status: 422,
          body: jsonError(`Joern query error: ${detail}`, { code: 'query_failed' }),
        };
      }
      const { nodes, edges, metadata } = parseAstTuples(stdout);
      if (!nodes.length) {
        return {
          status: 422,
          body: jsonError(`No AST found for scope=${scope}`, { code: 'empty_result' }),
        };
      }
      const label = methodFullName || fileName || `cpg(limit=${nodeLimit})`;
      return {
        status: 200,
        body: {
          nodes,
          edges,
          metadata,
          method_full_name: label,
          scope,
          ...(fileName ? { file_name: fileName } : {}),
        },
      };
    } catch (err) {
      if (err.code === 'query_timeout') {
        return { status: 504, body: jsonError('query timed out', { code: 'query_timeout' }) };
      }
      return { status: 502, body: jsonError(String(err.message || err), { code: 'joern_error' }) };
    }
  }

  return { handleCfg, handlePdg, handleDfg, handleAst };
}
