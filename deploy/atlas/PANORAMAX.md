# Optional private street-imagery companion

`compose.panoramax.yml` adds a separate Panoramax API, one picture worker, a migration job and dedicated PostGIS database. The profile is `imagery`; ordinary LeakScope startup does not start these services. No service publishes a host port. The API joins LeakScope's default network at `http://panoramax:5000/api`; the database, migration job and worker use a separate internal network. LeakScope's imagery proxy remains the browser-facing interface.

The stack begins with **no photographs**. It neither joins a federated catalog nor downloads pictures. Existing local imagery, if supplied, remains separate from IP geolocation: a nearby photograph is not proof that a device occupies the photographed building.

## Images and platform

The immutable references and provenance are in [panoramax-images.json](panoramax-images.json):

- `panoramax/api:2.17.0@sha256:dca7ee0df50c0409c1c6363533ed25e7f385e2c2f095963d9238c33af02fe5dd`
- `postgis/postgis:16-3.4@sha256:44126d872ac91993766c341e369c539e8196614321765d36a6f1bab0419a5fa5`

Both upstream images currently provide `linux/amd64`, declared explicitly for Docker Desktop emulation on this Apple Silicon host. API and worker limits are 512 MiB/0.75 CPU each; PostGIS is 256 MiB/0.5 CPU. Upload processing uses one worker thread. Larger locally supplied panoramas may require increasing the worker's memory limit. These are container images, not a worldwide imagery dataset.

## Private configuration

Generate two independent URL-safe random values and save them in the existing private deployment `.env`, without committing or printing them:

- `LEAKSCOPE_PANORAMAX_DB_PASSWORD`
- `LEAKSCOPE_PANORAMAX_SECRET_KEY`

The first is the companion database password; the second becomes Panoramax's `FLASK_SECRET_KEY`, which signs its administrative tokens. LeakScope's own database password and application secret stay separate.

All commands below run from the project folder. Use the same existing `.env` and data root as the application. The following preparation resolves Compose internally and creates the companion picture bind directory; it does not display the resolved private environment:

```sh
python3 - <<'PY'
import json
from pathlib import Path
import subprocess
command = ['docker', 'compose', '--env-file', '.env', '-p', 'leakscope',
           '-f', 'docker-compose.yml', '-f', 'deploy/compose.postgres.yml',
           '-f', 'deploy/atlas/compose.panoramax.yml', '--profile', 'imagery',
           'config', '--format', 'json']
config = json.loads(subprocess.check_output(command))
for service in ['panoramax']:
    for mount in config['services'][service].get('volumes', []):
        if mount.get('type') == 'bind':
            Path(mount['source']).mkdir(parents=True, exist_ok=True, mode=0o750)
PY
```

Pictures use the bind directory `${LEAKSCOPE_DATA_ROOT}/artifacts/atlas/panoramax/pictures`. PostgreSQL uses the project-scoped Docker named volume `<compose-project>_panoramax-db` (for example, `leakscope_panoramax-db`). It remains separate from LeakScope's production PostgreSQL. The volume lives in Docker's Linux storage and is not copied by backing up the picture folder.

The API, worker and migration job run with LeakScope's configured UID/GID. PostgreSQL uses its image's standard entrypoint and PostgreSQL user on Linux-managed storage. A named database volume avoids Docker Desktop ownership inconsistencies observed with PostGIS on external macOS bind mounts. `LOGNAME=leakscope-panoramax` supplies an audit identity for Yoyo migrations when that numeric UID has no entry in the API image's passwd database. It does not change the host user's environment.

## Start and inspect

Define a convenience function in the current shell:

```sh
panoramax_compose() {
  docker compose --env-file .env -p leakscope \
    -f docker-compose.yml -f deploy/compose.postgres.yml \
    -f deploy/atlas/compose.panoramax.yml --profile imagery "$@"
}
panoramax_compose config --quiet
panoramax_compose up -d --no-build panoramax panoramax-worker
panoramax_compose ps panoramax-db panoramax-migrate panoramax panoramax-worker
```

The dependency chain waits for an authenticated TCP query against the initialized Panoramax database, applies Panoramax's own schema, and then starts the HTTP API and worker. `api` is the upstream Waitress HTTP mode, with two request threads. `ssl-api` is deliberately avoided: it would advertise HTTPS picture links even though the internal service uses HTTP. Do not set external picture URL overrides; API-generated picture URLs must remain at the configured local origin for LeakScope's proxy.

Check the catalog and an empty nearby search from inside the service:

```sh
panoramax_compose exec -T panoramax python3 - <<'PY'
import requests
base = 'http://panoramax:5000/api'
response = requests.get(base, timeout=10)
response.raise_for_status()
search = requests.get(base + '/search', params={'bbox': '-71,39,-69,41', 'limit': 1}, timeout=10)
search.raise_for_status()
print('Catalog reachable; nearby retained photographs:', len(search.json().get('features', [])))
PY
```

A new instance correctly returns zero images. Once photographs exist, original and derivative assets are served by routes under `/api/pictures/<uuid>/...`; LeakScope accepts matching-origin imagery and proxies the permitted image formats. Source dates, coverage and approximate-location labels remain visible.

The pinned images were tested in an isolated Compose project on this Apple Silicon host on 2026-11-10: fresh PostGIS initialization, all schema migrations, healthy API and picture worker, catalog/search HTTP 200 with zero retained photographs, matching-origin API links, anonymous collection creation rejected with HTTP 401, and successful administrative-token CLI. No photographs were imported and large-photo processing was not load-tested. First startup includes emulated imports and database migrations, so it can take several minutes.

## Administrative access and local uploads

Reads are anonymous within the private Docker network. Collection creation and uploads require authentication (`API_FORCE_AUTH_ON_UPLOAD=true`); public registration stays closed. There is no OSM/OIDC dependency, external blur service, automatic public federation or user login added to LeakScope.

The upstream administrative CLI can obtain the default administrator's token. Save it directly into a private file, rather than displaying it:

```sh
umask 077
mkdir -p .runtime/private
panoramax_compose exec -T panoramax python3 -m flask default-account-tokens get \
  > .runtime/private/panoramax-admin-token.txt
chmod 600 .runtime/private/panoramax-admin-token.txt
```

Use that credential with the [official Panoramax upload CLI/API](https://docs.panoramax.fr/cli/USAGE/) from a client attached to the private Docker network. The companion API has no host port. Upload only imagery you intend to store on this private instance. No account creation or photo import is performed by LeakScope deployment.

## Backup, stop and updates

Back up the companion PostGIS database **and** picture directory together. For example, with the convenience function already defined:

```sh
umask 077
mkdir -p .runtime/private/panoramax-backup
panoramax_compose exec -T panoramax-db pg_dump -U panoramax -d panoramax -Fc \
  > .runtime/private/panoramax-backup/database.dump
```

Copy the resolved picture bind directory into the same private backup using the operator's normal filesystem backup tooling. The main LeakScope database dump does not contain Panoramax's photos or its separate schema.

To stop only the companion, retaining data:

```sh
panoramax_compose stop panoramax panoramax-worker panoramax-db
```

Update image digests deliberately, back up first, stop the API/worker and run the migration job before restarting them. Do not run a project-wide `down` command to stop this optional component; that would affect unrelated LeakScope services in the same Compose project.

## References

Configuration follows the current [Panoramax installation](https://docs.panoramax.fr/backend/install/install/), [settings](https://docs.panoramax.fr/backend/install/settings/), [administration](https://docs.panoramax.fr/backend/install/cli/) and [private-instance](https://docs.panoramax.fr/how-to-contribute/hosting-instance/) documentation. The correct connection-pool variables are `DB_MIN_CNX` and `DB_MAX_CNX`; the upstream sample's `BD_*` spelling is not used.
