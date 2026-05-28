# Joern Server

HTTP proxy and Docker deployment for [Joern](https://joern.io/) CPG analysis. Agents and services integrate via **CPGQL** on port **8080**.

## Documentation

Full docs: **[`docs/index.md`](docs/index.md)** (MkDocs: `pip install -r requirements-docs.txt && mkdocs serve`)

| Guide | Description |
|-------|-------------|
| [Architecture](docs/ARCHITECTURE.md) | Components, affinity, scaling |
| [Getting started](docs/getting_started.md) | First parse and query |
| [Query guide](docs/query_guide.md) | Parse modes, headers, CPGQL |
| [Deployment](docs/deploy.md) | Compose, HAProxy, monitoring |
| [Developer guide](docs/developer_guide.md) | Repo layout for contributors and agents |
| [Testing](docs/testing.md) | pytest and live tests |
| [API reference](docs/api_reference.md) | HTTP endpoints and Python API |

## Quick start

```bash
cp deploy/.env.example deploy/.env
docker compose -f deploy/compose.dev.yml up -d
curl -s http://127.0.0.1:8080/health | jq .
```

Scaled:

```bash
docker build -t neuralatlas-joern:local -f docker/Dockerfile .
docker compose -f deploy/compose.scale.yml up -d --scale joern=10
```

## Headers

| Header | Use |
|--------|-----|
| `X-Session-Id` | Agent run id (logging) |
| `X-Affinity-Key` | `sample_id` — HAProxy stickiness and REPL state |

## Example

```bash
curl -s -X POST http://127.0.0.1:8080/parse \
  -H 'Content-Type: application/json' \
  -d '{"sample_id":"demo","source_code":"int main(){return 0;}","language":"c"}'

curl -s -X POST http://127.0.0.1:8080/query-sync \
  -H 'Content-Type: application/json' \
  -H 'X-Affinity-Key: demo' \
  -H 'X-Session-Id: agent-1' \
  -d '{"query":"importCpg(\"/workspace/cpg/out/demo\")"}'

curl -s -X POST http://127.0.0.1:8080/query-sync \
  -H 'Content-Type: application/json' \
  -H 'X-Affinity-Key: demo' \
  -d '{"query":"cpg.method.name.l"}'
```

## Tests

```bash
pytest tests/unit/ -q
```

## License

MIT License — see LICENSE.
