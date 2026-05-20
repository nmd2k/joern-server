# Roadmap — Sprint 9 (HTTP-first platform)

Local copies of sprint backlogs and SDDs also live under `.pms/` (may be gitignored).

## Goals

1. **Remove MCP** — clients use HTTP only (`:8080`).
2. **Repo-level parse** — `POST /parse/repo` for whole project trees; keep `POST /parse` for single-file/snippet mode.
3. **Documentation** — architecture, client guide, full `http_api.md`.
4. **Benchmarks** — compare file-only vs repo CPG (latency + graph richness).
5. **Deploy cleanup** — two compose profiles (`dev`, `scale`); archive redundant YAML.

## API summary

| Endpoint | Mode | Input |
|----------|------|--------|
| `POST /parse` | File / snippet | `source_code` + `sample_id` (existing) |
| `POST /parse/repo` | Repository | `source_root` on allow-listed mount (v1); optional zip upload (v2) |

## Architecture (target)

```
HTTP clients → joern_server/proxy (:8080) → Joern REPL
```

No MCP SSE (`:9000`). Playground calls `/api/*` only.

## Deploy target

```
deploy/
├── compose.dev.yml
├── compose.scale.yml
├── haproxy.cfg
└── README.md   # single decision table
```

## References

- Session isolation (Sprint 8): see `.pms/docs/api/http_api.md` on branch `sprint/8`
- Detailed design: `.pms/docs/sdd/sdd_v2_http_platform.md`
- Backlog: `.pms/backlog/sprint9.md`
