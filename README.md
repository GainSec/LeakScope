# LeakScope

Search, browse, and monitor exposed services, data leaks and code leaks using Shodan.
![LeakScope Example](https://gainsec.com/wp-content/uploads/2025/11/leakscope-example.png)

## Features
- Shodan powered discovery for GitLab, Elasticsearch, Kibana, Jenkins, MongoDB, Rsync, FTP, Cassandra, CouchDB, RethinkDB, S3 bucket checks (open + brute force), Angular apps, JS secrets, Grafana, Prometheus, MinIO, Swagger/OpenAPI, Mongo Express, OpenSearch, and key patterns.
- Dashboard with counts, charts, and progress of reviewed findings.
- Browse/triage: confirm, mark for later, or delete (deletes add the IP/bucket to the blacklist).
- Monitoring: daily email digest of new findings.

## Requirements
- Docker with Compose (recommended) or Python 3.9+, Redis.
- Shodan API key (paid/membership with query credits).

## Environment variables
| Name | Purpose | Required |
| --- | --- | --- |
| `SHODAN_API_KEY` | Shodan API key used for all searches | Yes |
| `GMAIL_EMAIL` | Gmail address for daily monitoring emails | Optional |
| `GMAIL_PASSWORD` | App password for the Gmail account | Optional |
| `BLACKLIST` | Comma-separated IPs/hosts/buckets to ignore on import | Optional |
| `DJANGO_ALLOWED_HOSTS` | Comma-separated hostnames/IPs if you override ALLOWED_HOSTS (not needed with current `['*']`) | Optional |

## Quick start (Docker Compose)
1) Set env vars (e.g. in `.env`):
```
SHODAN_API_KEY=your_key_here
GMAIL_EMAIL=you@example.com          # optional
GMAIL_PASSWORD=app_password_here     # optional
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
celery -A leaklooker worker --loglevel=info --concurrency=1 --pool=solo &
celery -A leaklooker beat --loglevel=info &
python manage.py runserver 0.0.0.0:8000
```

## Blacklist handling
- Runtime blacklist = `BLACKLIST` env values + entries stored in the `BlacklistEntry` table.
- Deleting a finding in the UI adds its IP/bucket to `BlacklistEntry`; future imports skip it.

## Monitoring email
- Set `GMAIL_EMAIL` and `GMAIL_PASSWORD` (app password recommended).
- Monitoring tasks send a daily digest of new findings that aren’t already in the DB/blacklist.

## Data and persistence
- Default DB is `db.sqlite3` (mounted in Docker). Keep it if you want historical data.
- Static assets/templates live under `leaklooker_app/static` and `leaklooker_app/templates`.

## Notes
- Uses Django dev server in Docker for convenience; switch to gunicorn for production if needed.
- Shodan usage consumes credits; set sensible keywords/filters to control volume.
- This tool is for defensive/offensive security use.

## Acknowledgements

[Koa's s3 Bucket Name List](https://github.com/koaj/aws-s3-bucket-wordlist/blob/master/list.txt)
[Wojciech's LeakLooker-X](https://github.com/woj-ciech/LeakLooker-X)
