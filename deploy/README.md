# Deploy

Short pointer — full guide: **[`docs/deploy.md`](../docs/deploy.md)**.

## Profiles

| Profile | Command |
|---------|---------|
| Dev | `docker compose -f deploy/compose.dev.yml up -d` |
| Scale | `docker compose -f deploy/compose.scale.yml up -d --scale joern=10` |
| Monitoring | Add `-f deploy/compose.monitoring.yml` |

## Build image

```bash
docker build -t neuralatlas-joern:local -f docker/Dockerfile .
```

## Headers (scale)

Send `X-Affinity-Key: <sample_id>` on every `/query-sync` after parse. See [docs/query_guide.md](../docs/query_guide.md).
