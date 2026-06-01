import express from 'express';
import { createProxyMiddleware, fixRequestBody } from 'http-proxy-middleware';
import path from 'path';
import { fileURLToPath } from 'url';
import fs from 'fs';
import {
  collectCpgSamples,
  filterAndPaginateSamples,
  resolveCpgPaths,
  shouldScanLocalArchive,
} from './cpg-lookup.js';
import { createGraphRouter } from './graph/routes.js';

const __dirname = path.dirname(fileURLToPath(import.meta.url));

// ── Configuration ──────────────────────────────────────────
const PORT = parseInt(process.env.PLAYGROUND_PORT || '3000', 10);
const JOERN_PROXY_URL = process.env.JOERN_PROXY_URL || 'http://localhost:8080';
const PLAYGROUND_DIR = path.resolve(__dirname, process.env.PLAYGROUND_DIR || './public');
const DATASETS_DIR = process.env.DATASETS_DIR || path.resolve(__dirname, '../datasets');

const cpgPaths = resolveCpgPaths({ repoRoot: path.resolve(__dirname, '..') });
const CPG_BASE_DIR = cpgPaths.cpgBaseDir;
const CPG_OUT_DIR = cpgPaths.cpgOutDir;
const CPG_ARCHIVE_DIR = cpgPaths.cpgArchiveDir;
const CPG_SID_MAP_PATH = cpgPaths.cpgSidMapPath;
const CPG_CONTAINER_OUT = process.env.CPG_CONTAINER_OUT || '/workspace/cpg/out';
const SAMPLES_CACHE_MS = parseInt(process.env.CPG_SAMPLES_CACHE_MS || '60000', 10);
let samplesCache = null;
let samplesCacheAt = 0;

async function fetchJoernCpgSamples(repairSidMap) {
  const qs = repairSidMap ? '?repair_sid_map=1' : '';
  const url = `${JOERN_PROXY_URL}/cpg/samples${qs}`;
  const resp = await fetch(url, { signal: AbortSignal.timeout(15_000) });
  const data = await resp.json();
  if (!resp.ok || !data.ok) {
    const err = new Error(data.error || data.detail || `Joern /cpg/samples returned ${resp.status}`);
    err.status = resp.status;
    err.payload = data;
    throw err;
  }
  return { ...data, source: 'joern' };
}

function listLocalCpgSamples(repairSidMap) {
  const { samples } = collectCpgSamples({
    cpgOutDir: CPG_OUT_DIR,
    cpgArchiveDir: CPG_ARCHIVE_DIR,
    sidMapPath: CPG_SID_MAP_PATH,
    containerOutPrefix: CPG_CONTAINER_OUT,
    repairSidMap,
  });
  const enriched = samples.map((s) => ({
    ...s,
    available: Boolean(s.in_out || s.archived),
  }));
  return {
    ok: true,
    samples: enriched,
    cpg_out_dir: CPG_OUT_DIR,
    cpg_archive_dir: CPG_ARCHIVE_DIR,
    cpg_container_out: CPG_CONTAINER_OUT,
    sid_map_path: CPG_SID_MAP_PATH,
    cpg_paths_resolved_from: cpgPaths.resolvedFrom,
    source: 'local',
  };
}

function getCpgSamplesPayload(repairSidMap) {
  if (!repairSidMap && samplesCache && Date.now() - samplesCacheAt < SAMPLES_CACHE_MS) {
    return samplesCache;
  }
  const payload = listLocalCpgSamples(repairSidMap);
  if (!repairSidMap) {
    samplesCache = payload;
    samplesCacheAt = Date.now();
  }
  return payload;
}

// ── Express App ────────────────────────────────────────────
const app = express();

// ── 1. Serve static playground files ─────────────────────
if (!fs.existsSync(PLAYGROUND_DIR)) {
  console.error(`Playground directory not found: ${PLAYGROUND_DIR}`);
  process.exit(1);
}

app.use('/playground', express.static(PLAYGROUND_DIR));

app.get('/graph', (req, res) => {
  res.sendFile(path.join(PLAYGROUND_DIR, 'graph', 'index.html'));
});
app.use('/graph', express.static(path.join(PLAYGROUND_DIR, 'graph')));

// ── 2. Playground-local API (must register before Joern proxy) ──
const localApi = express.Router();
localApi.use(createGraphRouter({ joernUrl: JOERN_PROXY_URL }));

localApi.get('/datasets', (req, res) => {
  if (!fs.existsSync(DATASETS_DIR)) {
    return res.json({ ok: true, datasets: [], datasets_dir: DATASETS_DIR });
  }
  try {
    const entries = fs.readdirSync(DATASETS_DIR, { withFileTypes: true });
    const datasets = [];
    for (const entry of entries) {
      if (!entry.isDirectory() || entry.name.startsWith('.')) continue;
      const repoDir = path.join(DATASETS_DIR, entry.name);
      const variants = fs.readdirSync(repoDir, { withFileTypes: true })
        .filter(e => e.isDirectory() && !e.name.startsWith('.'))
        .map(e => e.name);
      datasets.push({
        name: entry.name,
        variants,
        container_path: `/workspace/datasets/${entry.name}`,
      });
    }
    res.json({ ok: true, datasets, datasets_dir: DATASETS_DIR });
  } catch (err) {
    res.status(500).json({ ok: false, error: err.message });
  }
});

function paginateSamplesPayload(payload, req) {
  const { limit, offset, q } = req.query;
  if (limit === undefined && offset === undefined && !q) {
    return payload;
  }
  const page = filterAndPaginateSamples(payload.samples || [], { limit, offset, q });
  return {
    ...payload,
    samples: page.samples,
    total: page.total,
    limit: page.limit,
    offset: page.offset,
    has_more: page.has_more,
    paginated: true,
  };
}

localApi.get('/cpg/samples', async (req, res) => {
  const repairSidMap = req.query.repair_sid_map === '1';
  try {
    let payload;
    if (shouldScanLocalArchive(CPG_ARCHIVE_DIR)) {
      payload = getCpgSamplesPayload(repairSidMap);
    } else {
      try {
        payload = await fetchJoernCpgSamples(repairSidMap);
      } catch (joernErr) {
        console.warn(`[server] Joern /cpg/samples failed (${joernErr.message}); trying local scan`);
        payload = listLocalCpgSamples(repairSidMap);
        payload.source = 'local-fallback';
        payload.joern_error = joernErr.message;
      }
    }
    return res.json(paginateSamplesPayload(payload, req));
  } catch (err) {
    return res.status(500).json({ ok: false, error: err.message, payload: err.payload || null });
  }
});

localApi.get('/cpg/lookup', async (req, res) => {
  const sampleId = String(req.query.sample_id || '').trim();
  if (!sampleId) {
    return res.status(400).json({ ok: false, error: 'missing sample_id' });
  }
  try {
    if (shouldScanLocalArchive(CPG_ARCHIVE_DIR)) {
      const row = getCpgSamplesPayload(false).samples.find((s) => s.sample_id === sampleId);
      if (!row) {
        return res.status(404).json({ ok: false, code: 'not_found', sample_id: sampleId });
      }
      return res.json({
        ok: true,
        ...row,
        available: Boolean(row.in_out || row.archived),
        source: 'local',
      });
    }
    const url = `${JOERN_PROXY_URL}/cpg/lookup?sample_id=${encodeURIComponent(sampleId)}`;
    const resp = await fetch(url, { signal: AbortSignal.timeout(30_000) });
    const data = await resp.json();
    return res.status(resp.status).json(data);
  } catch (err) {
    return res.status(500).json({ ok: false, error: err.message });
  }
});

app.use('/api', localApi);

// ── 3. Proxy /api/* to Joern proxy ────────────────────────
const apiProxy = createProxyMiddleware({
  target: JOERN_PROXY_URL,
  changeOrigin: true,
  pathRewrite: { '^/api': '' },
  timeout: 900000,
  proxyTimeout: 900000,
  on: {
    proxyReq: (proxyReq, req, res) => {
      fixRequestBody(proxyReq, req);
      console.log(`[proxy] ${req.method} ${req.url} -> ${JOERN_PROXY_URL}${proxyReq.path}`);
    },
    proxyRes: (proxyRes, req, res) => {
      console.log(`[proxy] ${req.method} ${req.url} <- ${proxyRes.statusCode}`);
    },
    error: (err, req, res) => {
      console.error(`[proxy] error: ${err.message}`);
      if (!res.headersSent) {
        res.status(502).json({ error: `Proxy error: ${err.message}`, code: 'proxy_error' });
      }
    }
  }
});

app.use('/api', apiProxy);

// ── Root redirect ──────────────────────────────────────────
app.get('/', (req, res) => {
  res.redirect('/playground/index.html');
});

// ── Health check ──────────────────────────────────────────
app.get('/health', (req, res) => {
  res.json({
    ok: true,
    joern_proxy: JOERN_PROXY_URL,
    playground_dir: PLAYGROUND_DIR,
    datasets_dir: DATASETS_DIR,
    cpg_base_dir: CPG_BASE_DIR,
    cpg_out_dir: CPG_OUT_DIR,
    cpg_archive_dir: CPG_ARCHIVE_DIR,
    cpg_paths_resolved_from: cpgPaths.resolvedFrom,
    cpg_archive_has_entries: shouldScanLocalArchive(CPG_ARCHIVE_DIR),
  });
});

// ── Start ──────────────────────────────────────────────────
app.listen(PORT, () => {
  console.log(`[server] Playground server running on http://localhost:${PORT}`);
  console.log(`[server] Serving playground from ${PLAYGROUND_DIR}`);
  console.log(`[server] Datasets directory: ${DATASETS_DIR}`);
  console.log(`[server] CPG paths resolved from: ${cpgPaths.resolvedFrom}`);
  console.log(`[server] CPG out directory: ${CPG_OUT_DIR}`);
  console.log(`[server] CPG archive directory: ${CPG_ARCHIVE_DIR}`);
  console.log(`[server] SID map path: ${CPG_SID_MAP_PATH}`);
  console.log(`[server] Proxying /api/* -> ${JOERN_PROXY_URL}`);
});
