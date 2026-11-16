# LeakScope container deployment

LeakScope is a login-free private LAN/Tailscale workbench on port2627. The Phase 3 stack contains web, discovery worker, fetch worker, one scheduler, Redis, PostgreSQL, a built-in Certstream server and its ingestor, plus the self-hosted Firecrawl/SearXNG research stack. Only web publishes a host port. The application source and static assets are inside immutable images; persistent data lives under the data root.

Current images are `leakscope:3.6-discovery-modes-20261108` and `leakscope:3.6-capture-20261108`. Migration 0024 adds SavedLookup.page_size with a compatible default of 100 before new workers start. Earlier Phase1/2 instructions are historical rollback evidence.

## gRPC and OT extension

The capture image must extend the matching application image so the `h2` parser dependency and source agree. This extension needs no new service, exposed port, API credential or database migration. New application/capture image IDs and runtime evidence must come from its own verified release record; the 3.3 tags are retained as the preceding release.

Native gRPC uses clear HTTP/2 or certificate-verified TLS with `h2` ALPN according to the observed endpoint scheme, on any port. It retains actual application bytes and decoded status. No reflection, arbitrary application RPCs or generic PLC/OT control operations are added. Keep the existing private access, capture limits, budgets, persistent data and matching Django secret intact. Public source references and local protocol fixtures do not certify live provider coverage.

## Private deployment configuration

Canonical project: `/srv/leakscope` (use your own checkout path). The private mode600 `.env` selects images, bind paths, the scheduler profile and PostgreSQL. It contains a generated URL-safe database password, so never print full resolved Compose configuration or container environment. Provider credentials remain in an ignored mode600 `config.json` mounted read-only at `/run/secrets/provider_config`; UI integration edits are encrypted in PostgreSQL using the persistent `app/django-secret`.

A new installation needs these `.env` entries (choose private values without publishing them):

```dotenv
COMPOSE_FILE=docker-compose.yml:deploy/compose.postgres.yml
COMPOSE_PROFILES=scheduler
LEAKSCOPE_IMAGE=leakscope:3.1-firecrawl-20261107
LEAKSCOPE_CAPTURE_IMAGE=leakscope:3.1-capture-20261107
LEAKSCOPE_CERTSTREAM_IMAGE=leakscope/certstream:1.10.1-tail
LEAKSCOPE_DATA_ROOT=/srv/leakscope/.runtime/docker
LEAKSCOPE_CONFIG_SOURCE=/srv/leakscope/config.json
LEAKSCOPE_DB_PASSWORD=<generate with Python secrets.token_urlsafe(48)>
LEAKSCOPE_FIRECRAWL_PASSWORD=<generate a separate private token>
LEAKSCOPE_SEARXNG_SECRET=<generate a separate private token>
LEAKSCOPE_PORT=2627
```

Create `app`, `artifacts`, `redis`, `beat`, `postgres`, `certstream`, `firecrawl/postgres`, `firecrawl/redis`, `firecrawl/rabbitmq` and `firecrawl/searxng` beneath the selected data root before startup. Bind mounts use `create_host_path: false` so a missing data mount fails visibly. App processes use UID501/GID20 by default; adjust `LEAKSCOPE_UID` and `LEAKSCOPE_GID` on another host and verify ownership. PostgreSQL 18 uses `/var/lib/postgresql` as its mount root.

Keep `app/django-secret` paired with the database in backups. A mismatched secret leaves encrypted keys unavailable; model/base edits preserve ciphertext so restoring the matching secret recovers access. Explicit key replacement/clearing intentionally changes the stored credential.

## Build and start

`uv.lock` resolves Python dependencies and `requirements.txt` supplies hash-pinned Docker installation. Python, Redis, PostgreSQL and Certstream source/base images are pinned; capture tools use pinned versions/checksums and a signed Debian snapshot. `Dockerfile.capture` extends the same app release. Compose declares its web build as a build context so a fresh combined build orders the images correctly.

```sh
# Select fresh release tags in the private .env before building.
docker compose config --quiet
docker compose build
# On an empty installation, run migrations before starting the ingestor.
docker compose up -d --wait
```

For an existing SQLite installation, follow the separate migration procedure **before** starting app/ingestor services: ingestion creates application rows and import intentionally refuses a populated target. Never run an old image against a forward-migrated schema. Retain the matching earlier image, database and artifact snapshot.

An HTTPS proxy hostname such as `https://leakscope.example.internal/` can front the application. Pass that hostname in `DJANGO_ALLOWED_HOSTS` and its HTTPS origin in `CSRF_TRUSTED_ORIGINS`. Keep these explicit when overriding either environment variable; do not use a wildcard host list. The URL can terminate TLS at the existing proxy without changing private port2627.

Example private application addresses: `http://192.0.2.10:2627/` and `http://leakscope.example.internal:2627/`. No application login was requested. The private HTTP addresses and approved HTTPS proxy hostname use Django CSRF protection for mutations. Preserve both allowed-host and trusted-origin configuration. Docker must be running and the data volume mounted; restart policies do not start the engine or mount a drive.

## Built-in and external Certstream

The default Compose stack builds/runs the pinned self-hosted Certstream server and a separate ingestor. It pulls public CT logs directly, checkpoints cursors and exposes internal metrics. New/missing cursors start at the live tail; restored cursors resume within a bounded catch-up window with gap/drop reporting. It does not download historical global CT data.

Monitoring → Stream settings selects **Built-in** or **External**, with an explicit `ws://` or `wss://` URL. External failure never silently switches to another server. The built-in service remains installed/running when external mode is selected, making the default available again without installation. URL credentials/query tokens are masked in API responses; preserve an unchanged URL in settings edits.

The ingestor stores only matches against enabled watches, and automatically dispatches each watch's configured actions within cooldowns/budgets. Wildcard SANs are not concrete fetch targets and are omitted. Stream activity/lag is separate from indexed-provider update latency.

## Scheduling and retention

Run exactly one beat scheduler. The default scheduler checks due saved lookups each minute, refreshes fetch-tool health, and performs hourly export/capture housekeeping. It coalesces missed intervals rather than replaying an unbounded backlog. Durable schedule slots reserve one logical Run/task ID and stop after three caught broker publish failures. A process crash can leave a pending slot for recovery; this is not a claim of exactly-once publication. Duplicate deliveries leave the original execution state intact.

The old daily `Monitor` dispatcher is disabled by default to avoid overlapping work; `LEAKSCOPE_LEGACY_MONITORING=1` explicitly restores it for a reviewed migration. New schedules use saved lookups. Old production Monitor rows were checked before this release.

Capture artifacts expire after 30 days by default (`LEAKSCOPE_CAPTURE_RETENTION_DAYS`, minimum 1). Observation history and registered correlation evidence remain in the database; this housekeeping deletes expired registered files and their artifact rows, not arbitrary files. Expired execution leases are removed. Provider requests share pacing and Retry-After state across workers; daily lookup/fetch budgets count jobs, with page/result bounds separately configured.

## Status and operations

```sh
docker compose ps
docker compose logs --tail=100 web worker fetch certstream-ingestor
docker compose exec -T redis redis-cli ping
docker compose exec -T postgres pg_isready -U leakscope -d leakscope
curl -fsS http://127.0.0.1:2627/health/
```

Health endpoints do not query paid providers. Only web publishes a port; Redis/PostgreSQL/Certstream stay internal. Docker logs rotate at 10 MiB ×3 files/service. The fetch worker has its own targeted Celery healthcheck. The ingestor healthcheck verifies a recent heartbeat, while Monitoring displays stream state and last event separately.

Firecrawl defaults to `http://firecrawl:3002/v2` and needs no Firecrawl Cloud key. The deployment includes its own browser, NuQ PostgreSQL17 queue, Redis, RabbitMQ and SearXNG; only its API joins the application network, with no additional host ports. See [the research deployment guide](firecrawl/README.md) for the required private backend password, SearXNG secret, data directories and optional Brave Search API configuration.

Netlas, an external Firecrawl URL and the OpenAI-compatible endpoint/model are configured in the UI. Blank omitted keys preserve stored secrets; explicit Clear removes them. Missing configuration produces an actionable state. Automated verification uses fixtures; it does not spend provider/model credits.

## Backup, upgrades and rollback

Stop beat, drain discovery/fetch queues and stop incoming writes before a coordinated release backup. Retain the matching PostgreSQL custom dump, artifact tree, `app/django-secret`, private config/.env, exact image IDs, Redis/beat recovery state and source revision. Test `pg_restore` in a fresh database and compare model content, not just row counts. A bind mount is not a backup.

Before new writes, rollback stops only this Compose project and selects the matching prior image/data root. The preserved Phase 2 SQLite tree and native runtime are separate fallback points. After new writes, reconcile them or deliberately select the recorded recovery point. Never use `down -v`, prune unrelated containers, restart Docker Desktop, or run two schedulers as an upgrade step.

## Isolated verification

Use a distinct project, blank provider config, loopback2629 and separate data. The Phase 3 rehearsal harness under ignored `.runtime/staging3` additionally puts app/fixture/DB/Redis on an internal network, with only the built-in CT server on an outbound network. Its HTTP/WebSocket fixtures exercise real tool jobs and certificate-watch automatic fetching without probing third-party targets. Staging fixture data is never imported into production.

Repository checks:

```sh
uv sync --frozen
.venv/bin/python manage.py test --settings=leakscope.test_settings --noinput
.venv/bin/python -m unittest discover -s tests/runtime -v
npm test
.venv/bin/python manage.py makemigrations --check --dry-run --settings=leakscope.test_settings
```

The provider-gap release adds response queries with per-provider evidence descriptions and removes the global Milvus HTTP assumption. It requires matching app/capture images and no new migration or service. Back up PostgreSQL, artifacts, private environment/config and the matching Django secret before deployment.

## Discover collection modes

Migration0025 adds `SavedLookup.collection_mode` with blank/custom Python and database defaults. Apply it before new workers start. Existing custom actions/auto-fetch values remain unchanged; the database default also permits older workers to insert rows during rollback. Discover explicitly defaults to Passive and routes every provider/query strategy through normalized provider-only collection. Semi-passive, Screenshot and Full queue capability-based captures afterwards.
