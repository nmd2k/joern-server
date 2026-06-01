import express from 'express';
import { createGraphHandlers } from './service.js';
import { affinityHeadersFromRequest } from './joern-client.js';

/**
 * @param {{ joernUrl: string }} opts
 */
export function createGraphRouter({ joernUrl }) {
  const router = express.Router();
  router.use(express.json({ limit: '1mb' }));
  const { handleCfg, handlePdg, handleDfg, handleAst } = createGraphHandlers({ joernUrl });

  async function runHandler(handler, req, res) {
    try {
      const headers = affinityHeadersFromRequest(req);
      const result = await handler(req.body || {}, headers);
      return res.status(result.status).json(result.body);
    } catch (err) {
      return res.status(500).json({ error: err.message, code: 'internal_error' });
    }
  }

  router.post('/graph/cfg', (req, res) => runHandler(handleCfg, req, res));
  router.post('/graph/pdg', (req, res) => runHandler(handlePdg, req, res));
  router.post('/graph/dfg', (req, res) => runHandler(handleDfg, req, res));
  router.post('/graph/ddg', (req, res) => runHandler(handleDfg, req, res));
  router.post('/graph/ast', (req, res) => runHandler(handleAst, req, res));

  return router;
}
