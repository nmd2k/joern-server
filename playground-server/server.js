import express from 'express';
import { createProxyMiddleware } from 'http-proxy-middleware';
import path from 'path';
import { fileURLToPath } from 'url';
import fs from 'fs';

const __dirname = path.dirname(fileURLToPath(import.meta.url));

// ── Configuration ──────────────────────────────────────────
const PORT = parseInt(process.env.PLAYGROUND_PORT || '3000', 10);
const JOERN_PROXY_URL = process.env.JOERN_PROXY_URL || 'http://localhost:8080';
const PLAYGROUND_DIR = path.resolve(__dirname, process.env.PLAYGROUND_DIR || './public');

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

// ── 2. Proxy /api/* to Joern proxy ────────────────────────
const apiProxy = createProxyMiddleware({
  target: JOERN_PROXY_URL,
  changeOrigin: true,
  pathRewrite: { '^/api': '' },
  timeout: 900000,
  proxyTimeout: 900000,
  on: {
    proxyReq: (proxyReq, req, res) => {
      console.log(`[proxy] ${req.method} ${req.url} \u2192 ${JOERN_PROXY_URL}${proxyReq.path}`);
    },
    proxyRes: (proxyRes, req, res) => {
      console.log(`[proxy] ${req.method} ${req.url} \u2190 ${proxyRes.statusCode}`);
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
    playground_dir: PLAYGROUND_DIR
  });
});

// ── Start ──────────────────────────────────────────────────
app.listen(PORT, () => {
  console.log(`[server] Playground server running on http://localhost:${PORT}`);
  console.log(`[server] Serving playground from ${PLAYGROUND_DIR}`);
  console.log(`[server] Proxying /api/* \u2192 ${JOERN_PROXY_URL}`);
});
