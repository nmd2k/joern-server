import { parseMetadataTuples } from './scala-parse.js';
import { parseUpstreamSuccess, postQuerySync } from './joern-client.js';

/**
 * Batch-fetch metadata for node IDs via /query-sync.
 * @param {string} joernUrl
 * @param {string[]} nodeIds
 * @param {Record<string, string>} headers
 * @param {(url: string, query: string, headers: Record<string, string>) => Promise<{status: number, data: object}>} [queryFn]
 */
export async function fetchNodeMetadata(joernUrl, nodeIds, headers = {}, queryFn = postQuerySync) {
  if (!nodeIds.length) return {};

  const numericIds = [];
  for (const nid of nodeIds) {
    const n = Number.parseInt(nid, 10);
    if (!Number.isNaN(n)) numericIds.push(n);
  }
  if (!numericIds.length) return {};

  const metadata = {};
  const maxBatch = 50;

  for (let i = 0; i < numericIds.length; i += maxBatch) {
    const batch = numericIds.slice(i, i + maxBatch);
    const idList = batch.map((nid) => `${nid}L`).join(', ');
    const query =
      `cpg.all.id(${idList}).collectAll[AstNode].map(n =>` +
      ` (n.id, n.code, n.lineNumber, n.columnNumber,` +
      ` n.order, n.label)` +
      `).l`;
    try {
      const { status, data } = await queryFn(joernUrl, query, headers);
      if (status !== 200 || !parseUpstreamSuccess(data)) continue;
      const batchMeta = parseMetadataTuples(data.stdout || '');
      Object.assign(metadata, batchMeta);
    } catch {
      continue;
    }
  }

  return metadata;
}
