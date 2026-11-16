# Built-in certificate stream

LeakScope includes a self-hosted Certstream server and a separate database ingestor as default Compose services. The server reads public certificate-transparency logs; the ingestor filters events through enabled WatchRules and automatically queues only their configured fetch actions. No public demo feed is used as a silent fallback.

## Server image and recovery behavior

`deploy/certstream/Dockerfile` builds Linux ARM64 from upstream **v1.10.1**, with the source archive and Go/Alpine base images pinned by SHA-256. `recovery.patch` is a small auditable change with six Go regression cases run during the image build. The resulting binary identifies itself as **1.10.1-leakscope.1**.

Upstream v1.10.1 starts a log at index zero when recovery is enabled and that log has no saved cursor. Its one-off `create-index` command does not protect logs added during later hourly reconciliation. LeakScope's patch applies to both ordinary and static/tiled logs: missing/zero cursors bootstrap at the current signed-tree-head/checkpoint tail, existing recent cursors resume, and invalid/far-behind cursors return to the live tail. Replay is capped at **10,000 entries per log**. Larger gaps are deliberately skipped and reported, not represented as complete historical coverage.

The custom Prometheus counters `leakscope_certstream_tail_bootstraps_total` and `leakscope_certstream_recovery_gaps_total` expose those choices at the internal `/metrics` endpoint. Upstream log-fetch errors and websocket slow-consumer drops remain possible. A healthy HTTP/WS endpoint does not prove every CT operator is reachable. This is a live observation feed, not a complete certificate archive or an assurance that a named service is currently reachable.

`config.yaml` is a committed read-only normal configuration. The server keeps its upstream-compatible checkpoint JSON at `/data/certstream/ct_index.json`, backed by `${LEAKSCOPE_DATA_ROOT}/certstream`. The directory must exist before startup. Known cursors are saved periodically and on graceful shutdown. The server has no host-published port, runs as the configured app UID/GID, and has CPU/memory limits plus rotated logs. Kafka/NSQ forwarding is disabled.

```sh
mkdir -p "$LEAKSCOPE_DATA_ROOT/certstream"
docker compose build certstream
docker compose up -d --wait certstream
docker compose logs --tail=50 certstream
```

The default image tag is `leakscope/certstream:1.10.1-tail`; the release may set `LEAKSCOPE_CERTSTREAM_IMAGE` to an immutable versioned tag. Preserve that image and the checkpoint directory when updating.

## Ingestor and watch semantics

The app-image `certstream-ingestor` runs `python manage.py consume_certstream` after successful application migrations and healthy Redis. It intentionally does **not** depend on the built-in server's health: an explicitly selected external feed must work even when the local source is unavailable. The PostgreSQL override supplies the same `DATABASE_URL` to this service and the other app workers.

`StreamSettings` selects exactly one mode:

- `builtin`: connect to `ws://certstream:8080/` (override only through the operator's `LEAKSCOPE_CERTSTREAM_BUILTIN_URL`).
- `external`: use the configured `ws://` or `wss://` URL. An empty/invalid URL is a visible configuration error; do not switch back to builtin automatically.

The consumer reconnects with bounded backoff, pings every 30 seconds, reloads settings/watches every ten seconds and flushes health/counters in batches. Frame/message bounds reject messages over 1 MiB, including fragmented messages. Configuration errors and external endpoint credentials are not reflected into logs or StreamHealth. Health tracks connection state, heartbeat, last certificate-event time and received/matched/dropped counts; unmatched global events do not create observation rows.

Domain matching normalizes IDNA, respects DNS label boundaries and supports a `*.example.com` pattern that matches subdomains but not the apex. Keyword rules are literal case-insensitive matches, not arbitrary regular expressions. Wildcard certificate SANs are not treated as concrete hosts to fetch. At most 100 concrete names and 16 configured ports are considered per event/watch. Missing ports default to 443. HTTP/HTTPS schemes are inferred only for common configured web ports; other ports retain an unspecified scheme.

Matching observations include bounded certificate metadata, reported log reference/index, provider timestamp and the fact that the port was **configured by the watch**, not observed open in the CT log. Tags inherit with a separate watch origin, preserving manual assignments. Persistent leases deduplicate repeated certificates/precertificates across logs; configured action cooldowns/budgets are enforced by the shared queue dispatcher. An observe-only watch stores matching evidence but does not fetch targets.

Useful bounded smoke-test commands:

```sh
docker compose run --rm --no-deps -T certstream-ingestor python manage.py consume_certstream --once --max-seconds=20
docker compose run --rm --no-deps -T certstream-ingestor python manage.py consume_certstream --max-events=10 --max-seconds=30
```

Do not launch a second continuous ingestor as an operational default. The bounded commands are for isolated staging fixtures or a planned brief check; their counters indicate messages consumed by that check.

## Verified evidence

Eight Django fixture tests cover IDNA/boundary matching, duplicate prevention, tag inheritance, automatic versus observe-only actions, explicit source mode, invalid/oversized input and batched consumer health. Three runtime tests cover default internal services, pinned build/recovery tests and persistent configuration. Six Go recovery cases execute in the actual server image build.

An isolated real server received certificate updates over its internal websocket, produced nonzero persistent cursors for reachable logs, and retained cursors across restart. An app-image consumer connected to that server, consumed one real event, updated heartbeat/received state and persisted no unmatched observations. No target fetches, paid provider searches or production changes were performed. Private evidence: `.runtime/certstream-rehearsal/verification.json`; build output: `.runtime/certstream-build.log`.

Sources: [upstream v1.10.1 watcher implementation](https://github.com/d-Rickyy-b/certstream-server-go/blob/v1.10.1/internal/certificatetransparency/ct-watcher.go), [upstream checkpoint storage](https://github.com/d-Rickyy-b/certstream-server-go/blob/v1.10.1/internal/metrics/logmetrics.go), [upstream release](https://github.com/d-Rickyy-b/certstream-server-go/releases/tag/v1.10.1).
