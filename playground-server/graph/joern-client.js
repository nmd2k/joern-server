const DEFAULT_TIMEOUT_MS = Number.parseInt(process.env.JOERN_QUERY_TIMEOUT_MS || '900000', 10);

export function parseUpstreamSuccess(respJson) {
  const success = respJson?.success ?? true;
  if (typeof success === 'string') {
    return ['true', '1', 'yes'].includes(success.trim().toLowerCase());
  }
  return Boolean(success);
}

export function joernStdoutHasError(stdout) {
  if (!stdout) return false;
  const text = stdout.trim();
  if (text.startsWith('-- [E') || text.startsWith('io.joern.console.Error')) return true;
  if (text.toLowerCase().includes('error found')) return true;
  return false;
}

/**
 * POST /query-sync on the Joern server.
 * @returns {Promise<{ status: number, data: object }>}
 */
export async function postQuerySync(joernUrl, query, headers = {}, timeoutMs = DEFAULT_TIMEOUT_MS) {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), timeoutMs);
  try {
    const resp = await fetch(`${joernUrl.replace(/\/$/, '')}/query-sync`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', ...headers },
      body: JSON.stringify({ query }),
      signal: controller.signal,
    });
    const data = await resp.json();
    return { status: resp.status, data };
  } catch (err) {
    if (err.name === 'AbortError') {
      const e = new Error('query timed out');
      e.code = 'query_timeout';
      throw e;
    }
    throw err;
  } finally {
    clearTimeout(timer);
  }
}

export function affinityHeadersFromRequest(req) {
  const headers = {};
  const affinity = req.headers['x-affinity-key'];
  if (affinity) headers['X-Affinity-Key'] = affinity;
  const session = req.headers['x-session-id'];
  if (session) headers['X-Session-Id'] = session;
  const reqId = req.headers['x-request-id'];
  if (reqId) headers['X-Request-Id'] = reqId;
  return headers;
}

export { DEFAULT_TIMEOUT_MS };
