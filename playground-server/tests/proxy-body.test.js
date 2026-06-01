import assert from 'node:assert/strict';
import express from 'express';
import { createProxyMiddleware, fixRequestBody } from 'http-proxy-middleware';
import http from 'node:http';
import { describe, it, after } from 'node:test';
import { createGraphRouter } from '../graph/routes.js';

describe('query-sync proxy body forwarding', () => {
  /** @type {import('node:http').Server | undefined} */
  let upstream;
  /** @type {import('node:http').Server | undefined} */
  let playground;
  /** @type {number | undefined} */
  let upstreamPort;
  /** @type {number | undefined} */
  let playgroundPort;
  /** @type {string | undefined} */
  let receivedBody;

  after(async () => {
    await new Promise((resolve) => upstream?.close(resolve));
    await new Promise((resolve) => playground?.close(resolve));
  });

  it('forwards JSON body to Joern when graph router does not consume it', async () => {
    upstream = http.createServer((req, res) => {
      const chunks = [];
      req.on('data', (c) => chunks.push(c));
      req.on('end', () => {
        receivedBody = Buffer.concat(chunks).toString('utf8');
        res.writeHead(200, { 'Content-Type': 'application/json' });
        res.end(JSON.stringify({ success: true, stdout: 'ok' }));
      });
    });
    await new Promise((resolve) => upstream.listen(0, '127.0.0.1', resolve));
    upstreamPort = /** @type {import('node:net').AddressInfo} */ (upstream.address()).port;

    const app = express();
    const localApi = express.Router();
    localApi.use(createGraphRouter({ joernUrl: `http://127.0.0.1:${upstreamPort}` }));
    app.use('/api', localApi);
    app.use('/api', createProxyMiddleware({
      target: `http://127.0.0.1:${upstreamPort}`,
      changeOrigin: true,
      pathRewrite: { '^/api': '' },
      on: {
        proxyReq: (proxyReq, req) => {
          fixRequestBody(proxyReq, req);
        },
      },
    }));

    playground = http.createServer(app);
    await new Promise((resolve) => playground.listen(0, '127.0.0.1', resolve));
    playgroundPort = /** @type {import('node:net').AddressInfo} */ (playground.address()).port;

    const payload = JSON.stringify({ query: 'importCpg("/workspace/cpg/out/demo")' });
    const resp = await fetch(`http://127.0.0.1:${playgroundPort}/api/query-sync`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', 'X-Affinity-Key': 'demo' },
      body: payload,
      signal: AbortSignal.timeout(5000),
    });

    assert.equal(resp.status, 200);
    assert.equal(receivedBody, payload);
  });
});
