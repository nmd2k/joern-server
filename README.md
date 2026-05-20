# Joern Server

HTTP proxy and deployment stack hosting [Joern](https://joern.io/) CPG analysis for LLM agent. 
Agent integrate via **CPGQL** on default port **8080**.

## Architecture

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

## Quick start

```bash
cp deploy/.env.example deploy/.env
# Edit deploy/.env (set JOERN_SERVER_AUTH_PASSWORD)

docker compose -f deploy/compose.dev.yml up -d
# HTTP API: http://127.0.0.1:8080
```

Scaled:

```bash
docker compose -f deploy/compose.scale.yml up -d --scale joern=4
```

See [deploy/README.md](deploy/README.md) for profiles.

## Example

```bash
# Parse snippet
curl -s -X POST http://127.0.0.1:8080/parse \
  -H 'Content-Type: application/json' \
  -d '{"sample_id":"demo","source_code":"int main(){return 0;}","language":"c"}'

# Load CPG and query
curl -s -X POST http://127.0.0.1:8080/query-sync \
  -H 'Content-Type: application/json' \
  -H 'X-Session-Id: my-session' \
  -d '{"query":"importCpg(\"/workspace/cpg-out/demo\")"}'

curl -s -X POST http://127.0.0.1:8080/query-sync \
  -H 'Content-Type: application/json' \
  -H 'X-Session-Id: my-session' \
  -d '{"query":"cpg.method.name.l"}'
```

See `deploy/README.md` for compose profiles and environment variables.

## Testing

```bash
pytest tests/unit/ -q
pytest -m integration   # needs running Joern
```

## Session affinity

Send the same `X-Session-Id` on every request when using HAProxy scale mode so `importCpg` state stays on one replica.

## References

- [Joern docs](https://docs.joern.io/)
- [deploy/README.md](deploy/README.md)

## License

MIT License — see LICENSE.
