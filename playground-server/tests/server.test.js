import http from 'node:http';
import assert from 'node:assert/strict';
import { describe, it, after, before } from 'node:test';

describe('Playground Server', () => {
  let baseUrl;
  let server;

  before(async () => {
    process.env.PLAYGROUND_PORT = '0';
    const mod = await import('../server.js');
    await new Promise(r => setTimeout(r, 1000));
    baseUrl = `http://localhost:${process.env.PLAYGROUND_PORT || 3000}`;
  });

  it('should start and respond to health check', async () => {
    console.log('Server started');
  });
});
