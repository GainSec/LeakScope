# LeakScope

LeakScope is a self-hosted workbench for finding, collecting and reviewing exposed services. It searches many different services and APIs, saves every result with its source and timestamps, captures screenshots and protocol evidence, watches certificate transparency for your domains, and lets you chain all of it into no-code workflows.

![LeakScope overview](docs/readme/home.png)

## Contents

- [Features](#features)
- [Installation](#installation)
- [Configuration](#configuration)
- [Using LeakScope](#using-leakscope)
- [Export and import](#export-and-import)
- [Updating and backups](#updating-and-backups)
- [Development](#development)
- [Credits](#credits)
- [License](#license)

## Features

**Find**

- **Discover** — keyword, network and country searches across Shodan, ZoomEye and Netlas, with service profiles for databases, CI servers, dashboards, storage, industrial protocols and more. The exact provider query is shown before anything runs.
- **Cloud discovery** — find storage buckets, databases and hosted applications from resource names.
- **Domain expansion** — grow a seed domain or an organization into candidate domains using DNS, web archives, web search and provider data.
- **Custom queries** — save your own provider queries with placeholders and rerun them.

**Evidence**

- **Observations** — every saved endpoint with its technology, tags, source history and captures, searchable and sortable.
- **Technologies** — what is exposed, grouped by product and category, with signature-based identification.
- **Screenshots** — a gallery of browser screenshots, native framebuffer captures and rendered protocol evidence, with collections, tags and filters.
- **Geography** — an interactive map of saved results by country and region.
- **Certificates** — certificates matched from the certificate transparency stream, searchable by domain or keyword.

**Scope**

- **Organizations** — group domains, addresses and saved results under an organization and see its exposure in one place.
- **Relationships** — a graph of how domains, addresses, certificates and services connect, each link backed by saved evidence.
- **Domain coverage** and **Correlations** — DNS evidence per domain and links between results that share identifying details.

**Watch**

- **Monitoring** — watch domains and keywords in the live certificate stream and act on matches automatically.
- **Scheduled lookups** — rerun saved searches on an interval or cron schedule, within request budgets you set.
- **What changed** — new, changed and removed results since the last collection.
- **Runs** — the full job history with logs for every search, capture and workflow.

**Automate**

- **Build** — a no-code workflow editor. Connect blocks that start from saved results, searches or events, then filter, sort, enrich, capture, tag and send. Each run states what it will spend and contact before it starts, and workflows can run on a schedule.

**Tools**

- **Inspect a target** — capture reachability, protocol responses, HTTP fingerprints or a screenshot from one saved endpoint, only when you allow it.
- **Service catalog** — profiles for 143 technologies with their provider queries and supported captures, plus your own technology signatures.
- **Research** — sourced query suggestions for a technology, ready to adopt or schedule.
- **Analysis** — charts of the collection over time, by source and by technology.

**Everywhere**

- Export any table to CSV or TXT, any single result to TXT, CSV or JSON, screenshots as a ZIP, and organizations as a formatted report.
- Export and import workflows and the whole database.
- Saved views, column choices and a command palette (`⌘K` / `Ctrl K`).

## Installation

LeakScope runs with Docker Compose. You need Docker with the Compose plugin and an API key for at least one of Shodan, ZoomEye or Netlas.

### 1. Get the code

```sh
git clone <repository-url> leakscope
cd leakscope
```

### 2. Create the data folders

```sh
export LEAKSCOPE_DATA_ROOT="$PWD/.runtime/docker"
mkdir -p "$LEAKSCOPE_DATA_ROOT"/{app,artifacts,redis,beat,postgres,certstream}
mkdir -p "$LEAKSCOPE_DATA_ROOT"/firecrawl/{postgres,redis,rabbitmq,searxng}
python3 deploy/init_browser_secret.py --data-root "$LEAKSCOPE_DATA_ROOT"
```

### 3. Add your provider keys

Create `config.json` in the project folder:

```json
{
  "config": {
    "SHODAN_API_KEY": "your-shodan-key",
    "ZOOMEYE_API_KEY": "your-zoomeye-key",
    "NETLAS_API_KEY": "your-netlas-key"
  }
}
```

Leave out any provider you do not use. Keys can also be added later under **Settings**.

### 4. Create `.env`

```dotenv
COMPOSE_FILE=docker-compose.yml:deploy/compose.postgres.yml
COMPOSE_PROFILES=scheduler
LEAKSCOPE_DATA_ROOT=/absolute/path/to/leakscope/.runtime/docker
LEAKSCOPE_CONFIG_SOURCE=/absolute/path/to/leakscope/config.json
LEAKSCOPE_PORT=2627
LEAKSCOPE_UID=1000
LEAKSCOPE_GID=1000
LEAKSCOPE_DB_PASSWORD=<random URL-safe value>
LEAKSCOPE_FIRECRAWL_PASSWORD=<random value>
LEAKSCOPE_SEARXNG_SECRET=<random value>
DJANGO_ALLOWED_HOSTS=localhost,127.0.0.1,leakscope.example.internal
CSRF_TRUSTED_ORIGINS=http://localhost:2627,https://leakscope.example.internal
```

Generate each random value with:

```sh
python3 -c "import secrets; print(secrets.token_urlsafe(48))"
```

Set `LEAKSCOPE_UID` and `LEAKSCOPE_GID` to the user that owns the data folders (`id -u` and `id -g`). List every hostname you will open LeakScope on in `DJANGO_ALLOWED_HOSTS`, and its full origin in `CSRF_TRUSTED_ORIGINS`.

### 5. Build and start

```sh
docker compose config --quiet
docker compose build
docker compose up -d --wait
```

Open **http://localhost:2627**. Database migrations run automatically on start.

### Check that it is running

```sh
docker compose ps
curl -fsS http://localhost:2627/health
docker compose logs --tail=100 web worker fetch
```

## Configuration

| Setting | Where | Purpose |
| --- | --- | --- |
| `SHODAN_API_KEY`, `ZOOMEYE_API_KEY`, `NETLAS_API_KEY` | `config.json` or Settings | Provider keys |
| `LLM_API_BASE`, `LLM_MODEL`, `LLM_API_KEY` | `config.json` or Settings | Optional model for Ask and Research |
| `CERTSPOTTER_API_KEY`, `URLSCAN_API_KEY` | `config.json` | Optional extra sources |
| `blacklist` | `config.json` | Addresses and names to skip when saving results |
| `LEAKSCOPE_PORT`, `LEAKSCOPE_BIND` | `.env` | Published port (default `2627`) and bind address |
| `DJANGO_ALLOWED_HOSTS`, `CSRF_TRUSTED_ORIGINS` | `.env` | Hostnames and origins LeakScope answers on |
| `LEAKSCOPE_DATA_ROOT` | `.env` | Where the database, captures and state are stored |
| `LEAKSCOPE_CAPTURE_RETENTION_DAYS` | `.env` | How long capture files are kept (default `30`) |
| `COMPOSE_PROFILES` | `.env` | `scheduler` runs scheduled lookups and workflows; add `imagery` for the street-level imagery companion |

### Services

| Service | Role |
| --- | --- |
| `web` | The application, on port 2627 |
| `worker` | Runs searches, lookups and workflows |
| `fetch` | Captures screenshots and protocol evidence |
| `beat` | Scheduler for lookups, workflows and housekeeping |
| `certstream`, `certstream-ingestor` | Built-in certificate transparency stream and watch matching |
| `postgres`, `redis` | Database and job queue |
| `firecrawl`, `searxng` | Web research companions |

## Using LeakScope

The sidebar groups pages by job: **Find** new results, review the **Evidence**, organize it by **Scope**, **Watch** for change, **Automate** with workflows, and a set of **Tools**. Press `⌘K` / `Ctrl K` anywhere to jump to a page by name.

![Jump to a page](docs/readme/palette.png)

### Overview

The home page summarizes the collection: saved endpoints, observations and evidence files, what is exposed by category, a polar map of located addresses, the live certificate stream, the technologies found, the latest captures and the latest jobs. Every mark opens the evidence behind it.

### Find

#### Discover

Pick a provider, enter a keyword or choose service profiles, and choose how far collection goes: passive (provider data only), semi-passive, screenshot or full capture. Narrow by network, country and exclusions, set request limits, and read the exact provider query before anything is sent. Recent discovery jobs stay listed with their saved output.

![Discover](docs/readme/discover.png)

#### Cloud discovery

Enter resource names and LeakScope checks for matching storage buckets, databases and hosted applications across AWS, Google Cloud, Azure, DigitalOcean, Firebase, Alibaba Cloud and other providers. Preview the candidate names first, cap the number of checks and the request rate, then review each resource with its assessment (public, access denied, exists) and add it to an organization.

![Cloud discovery](docs/readme/cloud.png)

#### Domain expansion

Start from a company name, a brand or a seed domain and collect candidate subdomains and related domains from local certificates, certificate transparency logs, passive DNS, web archives, legal entity records, reverse IP and web research. Each candidate shows its relationship, evidence strength, DNS status and the path that led to it. Accept, reject or tag candidates, add them to an organization, expand again from any one of them, or start monitoring it.

![Domain expansion](docs/readme/domain-expansion.png)

#### Custom queries

Save provider-specific queries with `{keyword}`, `{country}`, `{network}` and `{exclude}` placeholders, tag them, and run them again with new values.

![Custom queries](docs/readme/custom-queries.png)

### Evidence

#### Observations

Every saved endpoint, with its technology, tags, how many times it was observed and when. Search, filter by technology and source, sort all matching rows, choose columns, save the view, and select rows to tag or capture in bulk.

![Observations](docs/readme/observations.png)

**Inspect** opens the full record for one result: a summary, the source records from each provider, the history, the captures and the available actions. The level of proof is stated plainly, from discovery candidate to provider reported to confirmed live. Export the record as TXT, CSV or JSON.

![Inspect a result](docs/readme/inspect.png)

#### Technologies

What is exposed across saved results, by product and category, with how much of it has been captured live. Select a technology to open its observations and screenshots.

![Technologies](docs/readme/technologies.png)

#### Screenshots

Browse browser screenshots, native framebuffer captures and rendered protocol evidence as a gallery. Filter by technology, provider, tag, collection or image type, select images to keep, tag or group into collections, and export a selection as a ZIP.

![Screenshots](docs/readme/screenshots.png)

#### Geography

A map of saved results by approximate network location, as a flat map or a 3D globe, with counts by continent and country. Locations come from a local GeoIP database and can be compared with what each provider reported. Export the map as a PNG.

![Geography](docs/readme/geography.png)

#### Certificates

Search certificates matched from the certificate stream by domain or keyword, filter by watch and date, inspect any match, and add its names to an organization.

![Certificates](docs/readme/certificates.png)

#### Findings

Service-specific records saved by earlier versions, kept searchable alongside Observations.

### Scope

#### Organizations

Record an organization with its aliases, domains, networks and ASNs, then link saved results to it. Organizations can be merged, archived and exported.

![Organizations](docs/readme/organizations.png)

An organization's page brings its scope together: domains, linked results, evidence sources and technologies, with discovery and domain expansion one click away and a formatted report to export.

![Organization](docs/readme/organization.png)

#### Relationships

A graph of how organizations, domains, addresses, certificates and services connect. Focus on one asset or on recent ones, choose the relationship layer, and select any point or line to read the evidence behind it.

![Relationships](docs/readme/relationships.png)

#### Domain coverage

The DNS evidence saved for each domain: which record types are held, when they were collected and when they expire, for one organization or for every saved domain.

![Domain coverage](docs/readme/domain-coverage.png)

#### Correlations

Saved results that share an identifying value, such as a certificate fingerprint, a favicon hash, a host key or an identical response body, plus visually similar screenshots. Each group lists its members and where the value came from.

![Correlations](docs/readme/correlations.png)

### Watch

#### What changed

New, changed and removed records since you last looked, filtered by kind and organization. **Mark read** sets the baseline for next time.

![What changed](docs/readme/changes.png)

#### Monitoring

Watch domains and keywords in the live certificate transparency stream. Each watch saves matching certificates and can capture the matching hosts automatically, within a daily ceiling. The page shows the stream rate, how far behind live it is, and the latest matches. A built-in stream ships with LeakScope, or point it at your own.

![Monitoring](docs/readme/monitoring.png)

#### Scheduled lookups

Save a provider search and run it on an interval or a cron schedule, with a collection level, page and result limits, a daily run ceiling and tags for what it finds. Pause, edit or run any lookup by hand.

![Scheduled lookups](docs/readme/lookups.png)

#### Runs

Every job from queue to finish, on a timeline by kind and outcome. Open a run for its progress, logs and saved results, cancel work in progress, and export the whole history.

![Runs](docs/readme/runs.png)

### Automate

#### Build

Workflows chain LeakScope's jobs into steps that run in order. The list shows each workflow with what it contacts and when it last ran; export them all or import a file.

![Workflows](docs/readme/build.png)

In the editor, pick from 66 blocks: start from saved results, a search, chosen addresses or domains, an organization or a certificate match, then filter, sort, look up DNS and ownership, capture, tag and send. Set ceilings for one run, review what it will spend and contact, and run it by hand or on a schedule.

![Workflow editor](docs/readme/build-editor.png)

### Tools

#### Inspect a target

Pick a saved endpoint and capture evidence from it deliberately: reachability, a protocol request and response, an HTTP response and fingerprint, or a screenshot. Nothing is contacted until you allow it and submit. **Explore** does the same for stored services that can be browsed, such as indices, buckets and collections.

![Inspect a target](docs/readme/preview.png)

#### Service catalog

Profiles for 143 technologies across databases, storage, messaging, web applications, industrial protocols and devices, each with its provider queries and the captures it supports. Add your own technology signatures, including JA4S fingerprints imported from a packet capture.

![Service catalog](docs/readme/catalog.png)

#### Research

Search public documentation and repositories for how to find a technology, and get query suggestions for each provider with their sources. Edit a suggestion, open it at the provider, adopt it into your library or create a scheduled lookup from it. Requires the web research companion and, for written suggestions, a model endpoint.

![Research](docs/readme/research.png)

#### Analysis

Charts over the whole collection: totals, observations collected per day, and saved results by source and by technology.

![Analysis](docs/readme/analysis.png)

#### Export & import

Download saved results as CSV or JSON, filtered by technology, and maintain the blacklist of addresses and names to skip during collection.

![Export and import](docs/readme/export.png)

### Settings

Provider accounts and their status, optional integrations, service health, storage, runtime versions, and database export and import.

![Settings](docs/readme/settings.png)

## Export and import

| What | Where | Formats |
| --- | --- | --- |
| Any results table | **Export CSV** / **Export TXT** above the table | CSV, TXT |
| One saved result | **Export this result** in the Inspect dialog | TXT, CSV, JSON |
| Screenshots | **Export ZIP** on Screenshots (selected images, or every match) | ZIP with images, a CSV and an HTML page |
| An organization | **Export this organization** on its page | HTML report, TXT, CSV, JSON |
| The map | **Export PNG** on Geography | PNG |
| Workflows | **Export all** / **Import** on Build, **Export** in the editor | JSON |
| Saved results | **Export** page | CSV, JSON |
| The whole database | **Settings → Database** | Compressed archive, export and import |

## Updating and backups

Update by pulling the new code, rebuilding and restarting:

```sh
git pull
docker compose build
docker compose run --rm --no-deps migrate
docker compose up -d --wait
```

Back up the database before an update:

```sh
docker compose exec -T postgres sh -c 'pg_dump -U "$POSTGRES_USER" -Fc "$POSTGRES_DB"' > leakscope.dump
```

Keep `app/django-secret` from the data folder with your database backups: it protects the provider keys saved in Settings. You can also download a full copy from **Settings → Database → Export database**.

## Development

LeakScope is a Django application with Celery workers and a vanilla JavaScript front end.

```sh
uv sync                      # or: python -m venv .venv && pip install -r requirements.txt
npm install

# Python tests
.venv/bin/python manage.py test --settings=leakscope.test_settings --noinput

# Front-end tests
node --test tests/browser/*.test.cjs
```

Further reading:

- [Workflows and blocks](docs/build-workflows.md)
- [Capture tools](docs/CAPTURE.md)
- [Service fingerprints](docs/service-fingerprints.md)
- [Front-end system](docs/frontend-system.md)
- [Container deployment](deploy/README.md)

## Credits

- [Koa's S3 bucket name list](https://github.com/koaj/aws-s3-bucket-wordlist/blob/master/list.txt)
- [Wojciech's LeakLooker-X](https://github.com/woj-ciech/LeakLooker-X)

Made by [Jon "GainSec" Gaines](https://gainsec.com).

## License

LeakScope is free for personal use by individuals under the [LeakScope Personal Use License](LICENSE).

All other use needs a paid commercial license: companies, government bodies, research institutions, universities, non-profits, paid work delivered with LeakScope, and incorporating it into another product or service. See [COMMERCIAL.md](COMMERCIAL.md) or email [leakscope@gainsecmail.com](mailto:leakscope@gainsecmail.com).
