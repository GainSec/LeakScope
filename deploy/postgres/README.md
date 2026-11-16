# PostgreSQL deployment and SQLite migration

The Phase 3 runtime can use PostgreSQL without changing the preserved SQLite database. The base `docker-compose.yml` retains SQLite recovery support; load `deploy/compose.postgres.yml` for the approved PostgreSQL deployment. The override adds an internal-only PostgreSQL service and sets `DATABASE_URL` for web, worker, migrations and the single scheduler. Required bind paths must already exist.

Verified versions: PostgreSQL **18.6** (ARM64), psycopg/psycopg-binary **3.3.8**. PostgreSQL 18 is supported through November 2030; PostgreSQL 19 is still a release candidate in the checked version table. The image is pinned to `postgres:18.6-alpine@sha256:123b17998ccaf47e53b502e8c6a1e79afd8f579c6f3eec83c563da026f957395`. References: [PostgreSQL version policy](https://www.postgresql.org/support/versioning/), [official Docker image](https://hub.docker.com/_/postgres).

## Configuration

Keep `LEAKSCOPE_DB_PASSWORD` in the private mode-600 deployment `.env`; generate it using `secrets.token_urlsafe(48)` and do not print it. The Compose override uses that value in both PostgreSQL initialization and the application connection string, so use a URL-safe generated value. Application `DATABASE_URL` independently accepts percent-encoded credentials and IPv6 hosts. It allows only PostgreSQL schemes and explicitly supported connection options (`sslmode`, `connect_timeout`); validation errors never echo the URL.

Create `${LEAKSCOPE_DATA_ROOT}/postgres` before launch. PostgreSQL 18's data-directory layout is mounted at `/var/lib/postgresql`, not the pre-18 `/var/lib/postgresql/data` convention. Preserve this directory as part of the deployment, and use database dumps as backups rather than treating the bind mount as a backup.

```sh
docker compose -f docker-compose.yml -f deploy/compose.postgres.yml config --quiet
docker compose -f docker-compose.yml -f deploy/compose.postgres.yml up -d --wait postgres
```

For staging, use a separate project/data root and append `-f deploy/compose.staging.yml`; only web gets a loopback host port (2629). PostgreSQL has no published port. Never print the full resolved Compose configuration, environment or container inspection because it includes the private database password. The provider configuration remains a separate read-only Compose secret.

If the deployment `.env` sets `COMPOSE_FILE=docker-compose.yml:deploy/compose.postgres.yml`, ordinary `docker compose` commands also load PostgreSQL. Set this deliberately during cutover; merely adding a password does not switch the database. An empty/unset `DATABASE_URL` preserves the SQLite backend, and `leakscope.test_settings` explicitly selects its in-memory SQLite test database.

## Initial SQLite migration

Use the **same newly built app image** for source export, target migrations and import. Stop scheduling and incoming writes, drain jobs and stop web/worker before the final export. Retain the old image and a consistent SQLite/artifact/config backup. Run migration rehearsal with fixtures first; do not import a staging fixture into production.

The migration tool `deploy/postgres/migrate_sqlite.py` never changes the source SQLite file. It uses SQLite's backup API, migrates a private temporary copy to the current schema, exports a mode-600 fixture, and records model counts plus canonical serialized-content hashes in a separate mode-600 manifest. Primary keys are retained. Natural foreign keys resolve regenerated content types/permissions correctly; generated permissions/content types and sessions are not copied. Auth users/groups and their relationships are included.

Run from the canonical project with an explicitly selected new image and configured private deployment `.env`:

```sh
# Export the preserved SQLite DB using a private temporary snapshot.
# The output paths must not already exist.
docker compose -f docker-compose.yml -f deploy/compose.postgres.yml run --rm --no-deps -T \
  -e DATABASE_URL= web python deploy/postgres/migrate_sqlite.py export \
  --source /data/app/db.sqlite3 \
  --output /data/app/import/pre-postgres.json \
  --manifest /data/app/import/pre-postgres-manifest.json

# Start healthy PostgreSQL, migrate its empty schema, then import.
docker compose -f docker-compose.yml -f deploy/compose.postgres.yml up -d --wait postgres
docker compose -f docker-compose.yml -f deploy/compose.postgres.yml run --rm --no-deps -T migrate
docker compose -f docker-compose.yml -f deploy/compose.postgres.yml run --rm --no-deps -T \
  web python deploy/postgres/migrate_sqlite.py import \
  --fixture /data/app/import/pre-postgres.json \
  --manifest /data/app/import/pre-postgres-manifest.json
docker compose -f docker-compose.yml -f deploy/compose.postgres.yml --profile scheduler up -d --wait
```

Import refuses a nonempty application/auth-data target and a fixture that does not match its manifest. It runs the load, sequence reset and verification in one transaction; any row/hash mismatch rolls back the import. A repeated import cannot overwrite existing data. All affected sequences are reset so the next insert cannot collide with imported integer IDs. Existing overlong SQLite values that violate the declared PostgreSQL schema fail import rather than being silently truncated; reconcile such source data on a copy and repeat the rehearsal.

The tool prints only aggregate counts and success/failure categories, never fixture rows, connection strings or original subprocess error bodies. Export JSON contains private application data; keep it out of source control, build context and workbench publications. Move successful migration artifacts into the private release backup directory and apply the owner's retention policy.

## Backup and recovery

Create a PostgreSQL custom-format dump into a new private backup file. The shell's restrictive umask must be set before redirection:

```sh
umask 077
docker compose -f docker-compose.yml -f deploy/compose.postgres.yml exec -T postgres \
  pg_dump --format=custom --no-owner --no-acl -U leakscope leakscope > /private/backup/leakscope.dump
```

Verify restore into a fresh database with `pg_restore --single-transaction --no-owner --no-acl`; compare both model counts and content, not only the dump exit code. Retain the matching image, Django secret, artifacts and scheduler state. Database dumps are consistent snapshots; coordinating the matching artifact/queue recovery point still requires draining/stopping the relevant writers.

Before new PostgreSQL writes, initial rollback can stop the new app services, remove the PostgreSQL override from image/config selection, and recreate the prior image against the untouched SQLite database. Stop the project's PostgreSQL container without deleting its storage. After new writes, reconcile/export them or explicitly accept the recorded recovery point; do not pretend the old SQLite copy includes new observations. Never run an old image against an unverified newer schema and never use `down -v` as an upgrade step.

## Verification performed

The isolated `leakscope-postgres-rehearsal` project verified SQLite backup/export/import with fixed integer IDs, a foreign-key finding, an auth user/group/permission relationship and a JSONField with differing object-key order. PostgreSQL generated the next Search ID after the imported ID correctly. Four concurrent writers completed without errors. Re-import into a populated target was refused. A custom dump restored into a separate fresh database with matching per-model counts and canonical content hashes. No provider/model requests or production changes were involved. Private machine-readable evidence is `.runtime/postgres-rehearsal/verification.json`.


## Independent database storage

`LEAKSCOPE_POSTGRES_DATA_ROOT` overrides only the PostgreSQL bind source. Its fallback remains `${LEAKSCOPE_DATA_ROOT}/postgres`. This allows database data on internal APFS while code and artifacts stay on another volume. Create the directory before launch; `create_host_path: false` prevents an accidental empty path silently becoming a new database. PostgreSQL18 still mounts the parent at `/var/lib/postgresql`.

The optional `deploy/compose.postgres-native.yml`, loaded last, mounts the existing named volume specified by `LEAKSCOPE_POSTGRES_NATIVE_VOLUME` (default `leakscope_postgres_native`). A native volume uses Docker Desktop's virtual disk; it is not necessarily on the internal SSD. Check the actual Docker.raw location first.

Changing a mount does not migrate data. Quiesce all app writers, create a private `pg_dump -Fc`, restore to the intended PostgreSQL18 destination, and compare per-table row counts and SHA256 before opening traffic. Keep the original stopped database directory and dump available. Before reverting storage after new writes, back up and transfer those writes; never point back to a stale copy. Credentials and resolved Compose output stay private.
