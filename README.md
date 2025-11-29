# LeakScope

Search, triage, and preview exposed services and code leaks using Shodan.

## Features
- Shodan-powered discovery for GitLab, Elasticsearch/OpenSearch, Kibana, Jenkins, Mongo/Mongo Express, Rsync, FTP, Cassandra, CouchDB, RethinkDB, S3 buckets (open + brute force), Angular apps, JS secrets, Grafana, Prometheus, MinIO, Swagger/OpenAPI, Nexus, Artifactory, Docker Registry/Harbor (auth-free), key patterns, and more.
- Landing page with environment sanity (IP info, env var status) and a dark-themed UI across all pages.
- Home/Dashboard: bar chart of per-type counts, Shodan credit display, and total DB entries.
- Search: keyword-driven searches with passive/active probing toggle; Shodan metadata only unless “Enable Active Probing” is checked.
- Database: list all findings, hide entries (adds a fingerprinted “hidden” status) and view hidden list via toggle; quick Preview button per row.
- Custom Queries: build/save parameterized Shodan queries, fetch helper filters/facets, run them via Celery, and see results inline.
- Preview: active-probe preview (Mongo/Mongo Express, Elastic/OpenSearch, dirs, Jenkins, FTP, Couch, Rsync, S3) with progress polling.
- Export/Import: view records by type, export CSV/JSON, and import blacklist entries.

## Requirements
- Docker with Compose (recommended) or Python 3.9+, Redis.
- Shodan API key (paid/membership with query credits).

## Environment variables
| Name | Purpose | Required |
| --- | --- | --- |
| `SHODAN_API_KEY` | Shodan API key used for all searches | Yes |
| `BLACKLIST` | Comma-separated IPs/hosts/buckets to ignore on import | Optional |
| `DJANGO_ALLOWED_HOSTS` | Comma-separated hostnames/IPs (default `['*']` listens on all) | Optional |

## Quick start (Docker Compose)
1) Set env vars (e.g. in `.env`):
```
SHODAN_API_KEY=your_key_here
BLACKLIST=1.2.3.4,example-bucket     # optional
```
2) Build and run:
```
docker compose build
docker compose up -d
```
3) Apply migrations (first run):
```
docker compose exec web python manage.py migrate
```
4) Open the app at http://localhost:8000 (or your host IP:8000 on the LAN).

## Local run (without Docker)
```
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
export SHODAN_API_KEY=your_key_here
python manage.py migrate
redis-server &
celery -A leakscope worker --loglevel=info --concurrency=1 --pool=solo &
celery -A leakscope beat --loglevel=info &
python manage.py runserver 0.0.0.0:8000
```

## Blacklist handling
- Runtime blacklist = `BLACKLIST` env values + entries stored in the `BlacklistEntry` table.
- Deleting a finding in the UI adds its IP/bucket to `BlacklistEntry`; future imports skip it.

## Active probing toggle
- The UI has an `Enable Active Probing` checkbox on search, bucket brute-force, JS fetch, Git repo clone, and other network-touching flows.
- Default (unchecked): everything stays passive and relies only on Shodan metadata. Tasks that inherently require host contact will short-circuit and prompt you to enable the toggle.
- Checked: tasks may directly contact discovered hosts/endpoints (e.g., OpenSearch/Elastic node probes, S3 bucket brute-force, JS URL fetch, Git repo cloning) to enrich results.

## Data and persistence
- Default DB is `db.sqlite3` (mounted in Docker). You can delete it before committing; it will be recreated on first `migrate`. Keep it if you want historical data.
- Add `db.sqlite3` to `.gitignore` if you plan to track this repo.
- Static assets/templates live under `leakscope_app/static` and `leakscope_app/templates`.

## Notes
- Uses Django dev server in Docker for convenience; switch to gunicorn for production if needed.
- Shodan usage consumes credits; set sensible keywords/filters to control volume.
- This tool is for defensive/offensive security use.

## Page guide
- **Landing**: shows private/public IP, env var presence (green/red), total DB entries, CTA to Home, footer with GainSec.
- **Home**: stats bar chart (per-type counts), Shodan credits, total entries.
- **Search**: keyword form. Passive by default; check “Enable Active Probing” to contact hosts (required for some enrichments).
- **Database**: all findings with status column. “Hide” removes from main list and records fingerprint; hidden entries are viewable via expandable panel. Preview button links to active preview.
- **Custom Queries**: create/edit/save queries with placeholders `{keyword}`, `{country}`, `{network}`, `{exclude}`; optional default type and active-probe default; helper filters/facets pulled from Shodan (if API key present); run saved queries via Celery and view results.
- **Preview**: active probing preview for elastic/opensearch, dirs, jenkins, ftp, couchdb, mongo, mongoexpress, rsync, S3 bucket entries, Nexus/Artifactory, and Docker Registry/Harbor. Shows progress and summarised items (first 10 items/docs where applicable).
- **Export/Import**: choose a type to view rows (up to 500), export CSV/JSON, and import blacklist entries.
- **Explore**: type-aware explorer (Active Probing) to navigate stored findings (indices/dbs/repos/buckets/etc.) and export a targeted slice via `/explore/`.

## Active probing support
Preview currently supports: elastic, opensearch, dirs, jenkins, ftp, couchdb, mongo, mongoexpress, rsync, amazons3be/amazonbuckets (S3 style), nexus, artifactory, docker registry/harbor.
Other types are passive-only today; searching them relies on Shodan metadata unless explicitly probed elsewhere.

## Shoutouts

- **Koa's s3 Bucket Name List** - https://github.com/koaj/aws-s3-bucket-wordlist/blob/master/list.txt
- **Wojciech's LeakLooker-X** - https://github.com/woj-ciech/LeakLooker-X
