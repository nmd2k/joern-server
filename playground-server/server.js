import express from 'express';
import { createProxyMiddleware } from 'http-proxy-middleware';
import { Client } from '@modelcontextprotocol/sdk/client/index.js';
import { SSEClientTransport } from '@modelcontextprotocol/sdk/client/sse.js';
import path from 'path';
import { fileURLToPath } from 'url';
import fs from 'fs';

const __dirname = path.dirname(fileURLToPath(import.meta.url));

// ── Configuration ──────────────────────────────────────────
const PORT = parseInt(process.env.PLAYGROUND_PORT || '3000', 10);
const JOERN_PROXY_URL = process.env.JOERN_PROXY_URL || 'http://localhost:8080';
const MCP_SERVER_URL = process.env.MCP_SERVER_URL || 'http://localhost:9000/sse';
const PLAYGROUND_DIR = path.resolve(__dirname, process.env.PLAYGROUND_DIR || '../playground');

// ── Express App ────────────────────────────────────────────
const app = express();
app.use(express.json());

// ── 1. Serve static playground files ─────────────────────
// Verify playground directory exists
if (!fs.existsSync(PLAYGROUND_DIR)) {
  console.error(`Playground directory not found: ${PLAYGROUND_DIR}`);
  process.exit(1);
}

app.use('/playground', express.static(PLAYGROUND_DIR));

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

// ── 3. MCP Bridge ─────────────────────────────────────────
let mcpClient = null;
let mcpConnected = false;
let mcpConnectionError = null;

async function initMcpClient() {
  try {
    console.log(`[mcp] Connecting to MCP server at ${MCP_SERVER_URL}...`);
    const transport = new SSEClientTransport(new URL(MCP_SERVER_URL));
    const client = new Client(
      { name: 'joern-playground-server', version: '1.0.0' },
      { capabilities: {} }
    );
    await client.connect(transport);
    mcpClient = client;
    mcpConnected = true;
    mcpConnectionError = null;
    console.log('[mcp] Connected successfully');
  } catch (err) {
    mcpConnected = false;
    mcpConnectionError = err.message;
    console.error(`[mcp] Connection failed: ${err.message}`);
  }
}

// GET /mcp/tools — list available tools
app.get('/mcp/tools', async (req, res) => {
  if (!mcpConnected || !mcpClient) {
    return res.status(503).json({
      error: 'MCP server not connected',
      detail: mcpConnectionError || 'Connection not established',
      code: 'mcp_disconnected'
    });
  }
  try {
    const result = await mcpClient.listTools();
    res.json(result);
  } catch (err) {
    res.status(502).json({ error: err.message, code: 'mcp_error' });
  }
});

// POST /mcp/tools/:toolName — execute a tool
app.post('/mcp/tools/:toolName', async (req, res) => {
  if (!mcpConnected || !mcpClient) {
    return res.status(503).json({
      error: 'MCP server not connected',
      detail: mcpConnectionError || 'Connection not established',
      code: 'mcp_disconnected'
    });
  }
  const toolName = req.params.toolName;
  const args = req.body || {};
  console.log(`[mcp] Calling tool: ${toolName}`, args);
  try {
    const result = await mcpClient.callTool({
      name: toolName,
      arguments: args
    });
    res.json(result);
  } catch (err) {
    console.error(`[mcp] Tool error (${toolName}): ${err.message}`);
    res.status(422).json({ error: err.message, code: 'tool_error', tool: toolName });
  }
});

// POST /mcp/tools/:toolName/call — alternative path
app.post('/mcp/tools/:toolName/call', async (req, res) => {
  req.params = req.params;
  await app._router.handle(req, res);
});

// ── Root redirect ──────────────────────────────────────────
app.get('/', (req, res) => {
  res.redirect('/playground/index.html');
});

// ── Health check ──────────────────────────────────────────
app.get('/health', (req, res) => {
  res.json({
    ok: true,
    mcp_connected: mcpConnected,
    joern_proxy: JOERN_PROXY_URL,
    playground_dir: PLAYGROUND_DIR
  });
});

// ── Start ──────────────────────────────────────────────────
async function start() {
  await initMcpClient();
  app.listen(PORT, () => {
    console.log(`[server] Playground server running on http://localhost:${PORT}`);
    console.log(`[server] Serving playground from ${PLAYGROUND_DIR}`);
    console.log(`[server] Proxying /api/* \u2192 ${JOERN_PROXY_URL}`);
    console.log(`[server] MCP bridge \u2192 ${MCP_SERVER_URL}`);
    console.log(`[server] MCP connected: ${mcpConnected}`);
  });
}

start();
