# Joern Server Documentation Portal

Welcome to the documentation portal for **Joern Server**, the HTTP proxy and deployment stack hosting [Joern](https://joern.io/) Code Property Graph (CPG) analysis.

This platform allows LLM agents and remote clients to perform automated code analysis and querying using the **CPGQL** language on the default port **8080**.

---

## Quick Navigation

<div class="grid cards" markdown>

-   **[:material-image-filter-hdr: Architecture](ARCHITECTURE.md)**

    ---

    Understand the HTTP-first request flow, session isolation, and on-disk storage layouts.

-   **[:material-book-open-page-variant: Client Guide](CLIENT_GUIDE.md)**

    ---

    Explore code examples for parsing single snippets, NDJSON repositories, and querying sessions.

-   **[:material-server-network: Deployment](deploy.md)**

    ---

    Guides for scaling up Joern Server behind HAProxy in production environments.

-   **[:material-test-tube: Testing Guide](testing.md)**

    ---

    Learn how to run unit, integration, and load/stress tests for the proxy.

</div>

---

## Core Architecture Overview

```
HTTP client (agent, CI, playground)
      │
      ▼
joern_server/proxy.py  (:8080)
  • POST /parse              — single-file / snippet
  • POST /parse/repo         — repo via JSONL or upload_id
  • POST /query-sync         — CPGQL (importCpg, cpg.*, …)
  • POST /graph/*            — CFG, DDG, PDG, AST
  • POST /cleanup, GET /health, GET /version
      │
      ▼
Joern HTTP REPL (in-container, :8081)
      │
      ▼
/workspace/cpg-out/<sample_id>
```

---

## Quick Start (Local Run)

To run a local development instance of Joern Server:

1. **Configure Environment Variables**:
   ```bash
   cp deploy/.env.example deploy/.env
   # Edit deploy/.env and set JOERN_SERVER_AUTH_PASSWORD
   ```

2. **Start Dev Profile**:
   ```bash
   docker compose -f deploy/compose.dev.yml up -d
   # HTTP API will be exposed on: http://127.0.0.1:8080
   ```

3. **Check Health**:
   ```bash
   curl -s http://127.0.0.1:8080/health
   ```

For detailed multi-replica scaling options, see the [Deployment Guide](deploy.md).
