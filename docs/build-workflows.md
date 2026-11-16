# Build: workflows

Build (sidebar: Automate → Build, `/build/`) chains LeakScope's existing jobs into a workflow. Stage 1 is a
straight list of steps.

## Terms

- **Block**: one job with settings. It takes a list of items and gives a list.
- **Item**: a saved record with named fields. Stage 1 kinds: `endpoint` and `value`. Endpoint fields:
  `address, port, transport, scheme, technology, title, first_seen, last_seen, times_captured, tags`.
- **Workflow**: `Workflow.definition` = `{"steps": [{"id", "block", "settings"}], "limits": {"pages", "contacts", "items"}}`.
  Order is execution order. Later stages add branches; nothing should assume order is the only form.
- **Run**: a `WorkflowRun` with a frozen copy of the definition, attached to an ordinary `Run` of kind `workflow`.
  It appears on the Runs page, emits run events and can be stopped there.

## Blocks

| Group | Blocks | Reach |
|---|---|---|
| Starts | Run by hand; Saved results that match | local |
| Find | Search a provider; Related services on the host | spends credits |
| Collect | HTTP fingerprint; Fetch response; Screenshot; Protocol identity; Port answers | contacts target |
| Enrich | Netlas enrichment | spends credits |
| Enrich | Names for the address; Network owner; DNS records; Domain registration | public lookup |
| Organize | Tag; Add to organization | local |
| Filter, sort and flow | Filter; Keep only new results; Limit | local |
| Values | Take a value; Set a value | local |
| Domains | Starts: Saved domains that match; Names on saved certificates. Convert: Names of each result; Saved results for each domain. Organize: Watch in the certificate stream | local |
| Domains | Expand domains (from domains or a chosen organization; default public sources, optional web archives; optional web search and Netlas, which spend credits; optional Shodan, ZoomEye or Netlas searches for the organization's name in certificates and for host names under its first domain, one page each, whose names are passed on; waits for the expansion job) | public lookup, credits when a paid source is on |

| Find | Search for a service (catalogue query); Run a saved lookup | spends credits |
| Enrich | Location (local database; adds `country`, `country_code`, `city`) | local |
| Filter, sort and flow | Sort; Remove duplicates | local |
| Send | Web request | sends out |
| Send | Export file (CSV, Markdown table, plain text or JSON, downloadable from the run for a day) | local |
| Find | Run a custom query (a saved Custom query, with keyword, country, network and exclusions); Find similar (same favicon, certificate, server, product or device type) | spends credits |
| Find | Cloud discovery (names from the step or from incoming domains; ten names per cloud run) | contacts target |
| Starts | Saved cloud resources | local |
| Collect | Read industrial points (Modbus TCP, BACnet/IP; read-only); Port inventory (active TCP scan of each IP address); Check for a web interface | contacts target |
| Filter, sort and flow | Has a screenshot | local |
| Filter, sort and flow | If (Yes and No sides) | local |
| Organize | Add domains to organization; Add to organization can create one by name | local |
| Starts | Domains of an organization | local |
| Find | Discover an organization (ARIN and RIPE registry records for networks; optional Netlas, which spends credits and can return domains; waits for the job) | public lookup, credits with Netlas |
| Organize | Add screenshots to a collection; Accept or reject domains (through the domain expansion review, so decisions and audit evidence are saved the same way) | local |
| Organize | Remove tags (whoever added them) | local |
| Filter, sort and flow | Stop if (more or fewer than a number; later steps are skipped); Count (per value of a field; gives counts that Export file can save); Wait (1 to 60 minutes) | local |
| Enrich | Cloud network (adds `cloud_provider`, `cloud_service`, `cloud_region` from the published cloud ranges held locally) | local |
| Filter, sort and flow | Has saved DNS records (domains with or without, as Domain coverage shows) | local |
| Enrich | Microsoft tenant lookup (one public request per domain) | public lookup |
| Find | Saved results that share evidence (same certificate, favicon, tracking ID; from saved correlations) | local |
| Filter, sort and flow | Run another workflow (as one step; one level deep) | local, plus whatever that workflow reaches |
| Starts | When a search or lookup finishes (automatic; searches made by workflows do not count) | local |

**Web request.** Any method, address, headers and body, built from text and `{{values}}`; with a value it is sent once per item (identical requests once), otherwise once. Only http and https. Unless "Allow internal addresses" is ticked, a host that resolves to a loopback, link-local, private, reserved or multicast address is refused. Redirects are not followed, the request times out after 15 seconds, at most 64 KiB of the reply is read, and a step sends at most its own limit (default 50, maximum 500). The reply is available as `{{response_status}}` and `{{response_body}}` (first 2,000 characters). Headers are stored with the workflow as written, so a token placed there is readable by anyone who can open the workflow.

**Checkbox settings** use `Field(..., 'flag')`. A block that always adds the same values declares `adds_fixed`.

**Kinds.** A workflow carries `endpoint` or `domain` items (`leakscope/build/kinds.py`: `FIELDS`, `domain_item`). A block's `takes` is a kind, a tuple of kinds or `'any'`; `gives` is a kind or `'same'`. Filter, Limit, Take a value, Set a value, DNS records and Domain registration work on both kinds. `engine.validate` tracks the kind flowing through and the editor greys out blocks that need the other kind, saying which converter to add.

**Saved results that match** filters by address or title text, technology, tag, source, organization, port and age, and
by an optional "Only when" condition on any item field. With a condition it checks the newest 20,000 matching records.

**Item fields** (`leakscope/build/kinds.py`, `ENDPOINT_FIELDS`): `address, ip, hostname, names, port, protocol, transport,
scheme, technology, title, sources, tags, times_captured, first_seen, last_seen`. `names` and `sources` are read from the
saved observations; nothing is looked up. `protocol` is the scheme, or the transport when there is none.

**Comparisons** (`leakscope/build/conditions.py`): is, is not, is one of, contains, does not contain, starts with, ends
with, matches pattern, does not match pattern, is empty, is not empty, greater than, less than. Patterns are regular
expressions, case-insensitive, at most 200 characters; a repeated group that itself repeats is refused.

**Values.** A setting marked `template=True` (Search query, Tag tags, Set a value text) may contain `{{name}}`, filled per
result from its fields. Take a value names a field or a pattern match on it; Set a value builds text from other values. Named
values travel with the result through later steps. `engine.validate` rejects a `{{name}}` that no earlier step provides. A
Search whose query uses a value runs once per distinct rendered query ("for each result"), within the page ceiling.

**Only when (if / otherwise).** Any step that takes and gives results can carry `"when": {conditions}`. The step runs for the
matching results; the others pass through untouched and rejoin after it. Two steps with opposite conditions are an if / otherwise.

**Per-result jobs** (`blocks/enrich.py`, `PerResultJob`): a block that starts one existing job per result subclasses it
and implements `launch(ctx, item, settings)`, returning a queued Run id, `True` when saved evidence already answers it, or
`None` when the result does not apply (for example DNS records for an IP address). Set `spends = 'pages'` or `'contacts'`
to count against a ceiling. Netlas enrichment and Related services count one provider page per result.

**Fields that draw on saved data**: `Field(..., options='organizations')` renders a choice from the list the API returns
under `options`; `Field(..., suggest='technologies')` or `'tags'` offers suggestions while typing.

Search and Capture call `queue_lookup` and `queue_capture`, so daily budgets, the one-hour capture cooldown,
the blocklist and provider limits are enforced by those jobs exactly as on their own pages. Search reuses one
saved lookup per workflow, provider and query (a stable id), so that lookup's daily run budget accumulates
across runs and the Scheduled lookups list gains one row per search, not one per run.

## Running a workflow inside another

Run another workflow starts a saved workflow as one step and waits for it (up to 60 minutes).

- "The results that reach this step" replaces the other workflow's start with those results; its first step is recorded as "Given N by …". The kinds must agree (a workflow that starts with domains cannot be given results); a mismatch fails the step with that reason.
- "Its own start, as usual" simply runs it.
- What the other workflow can spend is added to this workflow's estimate, so the one confirmation covers both, and changing the other workflow asks for confirmation again (an automatic workflow is turned off with a note, as for any change).
- The other workflow keeps its own ceilings, lowered to what this run has left. Its pages and contacts count against this run.
- One level only: the other workflow may not itself contain this block. It appears in Runs as its own run. If it is already running, the step fails and says so.
- Results pass through this step unchanged; what the other workflow did is in its own run.

There is no "for each" block because steps already work per item: a Search whose query uses `{{value}}` runs once per distinct value, and per-result jobs run once per result.

## Sorting and filtering

Sort and Filter work on results and on domains, anywhere after a start. The block library has a row of group buttons; "Filter, sort and flow" shows them with Limit, If, Remove duplicates and Only these.

- Sort orders by any field. "A to Z · smallest · oldest first" or "Z to A · largest · newest first"; items that lack the field go last either way. The step note says which way it sorted.
- Domains carry `added`: when the domain was first saved (domain expansion), first put in the organization's scope, or when its certificate was first seen. Chosen domains and names taken from results have no date.
- Filter keeps items by conditions on any field, for example `domain` contains `dev`.
- Sorting only changes what later steps do when a Limit follows or a ceiling stops the run part-way.

## Drilling down to one address or domain

- **Chosen saved results** starts with the saved results at exactly the listed addresses (one, several; `address:port` for one service).
  **Chosen domains** and **Chosen addresses** start with listed names or IP addresses whether or not anything is saved for them.
  "All" remains Saved results that match and Saved domains that match.
- **Only these** keeps, part-way through, only the listed addresses or names (or everything except them).
- **Look up each address** asks a provider one query per address, built from the provider's verified filter with the value
  quoted and escaped (`ip:"192.0.2.1"`, `hostname="app.example.com"`, `host:"app.example.com"`), and passes on only results
  whose address or saved names are that address (optionally its subdomains). Anything else the provider returned is saved
  as usual but left out of the flow and counted in the step's note.
- A `{{value}}` put into a query is inserted as text: its quotes and backslashes are escaped, so a title or name cannot end
  the quoted term it sits in and add terms of its own.
- Every search step's note ends with "Asked:" and the exact text sent to the provider (first five queries).
- List settings take up to 500 entries, one per line. The editor can search saved addresses and domains (`GET /api/build/pick`) and tick them.

## Branches

A step takes its results from the steps listed in its `after` (ids of earlier steps; `id:yes` or `id:no` for a side of an If).
Without `after` it follows the step above it, so a plain chain is stored exactly as before and its confirmation stays valid.

- **If** splits results into a Yes side and a No side. A later step must name the side it follows.
- Two steps can follow the same step; each gets all of its results.
- A step can follow several steps. Their results arrive together, each once. They must be the same kind.
- A step that no results reach does not run and is marked Skipped, so an empty side costs nothing.
- What a run ends with (and what "Open results in Observations" shows) is everything that reached the end of any path.
- Steps run in list order; the editor keeps every step below the steps that feed it.

## The editor

The map above the step cards shows every step as a box and every connection as a line. Press the dot under a box
(Yes or No under an If), then press the step that should come next, or pick a block in the library to add one there;
dragging from the dot to a box does the same. Press a line, then Remove connection, to remove it. Each step card
has the same connections under "Comes after", usable without a pointer. After a run, the map shows each step's state
and counts until the workflow is edited. The map scrolls inside its own frame.


The block library is always on screen beside the steps: every block, grouped, with a one-line description (`says` on the block class) and a search box that matches name, description, group and reach. A block that cannot go next is greyed out with the reason. Clicking a block appends it; clicking a start block replaces step 1. A new workflow starts empty.

## Running

1. `POST /api/build/workflows/<id>/estimate` returns the worst case (`pages`, `contacts`, `items`) and a `token`.
2. The page shows those numbers and waits for Run. This is the only confirmation; nothing pauses mid-run.
3. `POST /api/build/workflows/<id>/run` with the token starts the run. A missing or stale token returns 409
   with a fresh estimate, so a run can never start from numbers the person did not see.

The engine (`leakscope/build/engine.py`, task `leakscope.build.advance`) works in short slices. A block returns
`Done` or `Waiting`. A waiting step is polled every 5 seconds until its jobs finish, and fails after 60 minutes.

A slice claims the step under a row lock and commits before the block runs, so jobs a block queues can always
see the records it created, and a slice delivered twice cannot start a step twice. One workflow has one active
run at a time; a second confirmation is refused. If the runner itself breaks, the run is marked failed; if its
polling stops silently, the run is reported lost after the usual run deadline of inactivity.

## Automatic starts

A workflow can start by itself. Turning it on (`POST /api/build/workflows/<id>/enable` with the estimate token, plus
`schedule_minutes` 5–43200 unless its first block is an event start) is the one confirmation; later runs do not ask.
`leakscope.build.tick` runs every minute from Celery beat and, for each enabled workflow:

- switches it off with a note if its definition no longer matches the confirmed estimate or has problems (saving an
  edited workflow does the same at once);
- skips it while a run is active, and when today's runs have reached `limits.runs_per_day` (default 24, maximum 288);
- starts a run when the schedule is due, or, for an event start, when the block's `pending()` finds records newer than
  the workflow's cursor. The run takes only records newer than that cursor (`Context.since`).

Event starts (`blocks/events.py`, `event = True`): When new results are saved; When a result changes; When a certificate
matches a watch. Run by hand, they use the last day. Each takes at most 500 records per run.

## Ceilings

Per run: provider pages (default 5, max 200), target contacts (25, max 1000), items per step (500, max 5000).

- A block is given only what remains. Capture queues as many endpoints as fit; Search fetches as many pages as fit.
  Items that did not fit are counted (`left_out`), the run continues with the rest and ends `partial`.
- If nothing remains when a paid or target-contacting block comes up, the run ends there; later steps are `skipped`.
- A partial run is `succeeded` with `partial: true` and `stopped_at: {step, name, reason}`.

A failed capture does not fail the run. A failed search, a block error, a missing block or a one-hour wait does.

## Adding a block

Create a file in `leakscope/build/blocks/`. Every module in that folder is imported, so nothing else registers it.

```python
from leakscope.build.kinds import refresh, gone
from .base import Block, Field, Cost, Done, register
from .starts import ENDPOINT_FIELDS


@register
class OnlyPort(Block):
    key, name, group, reach = 'only_port', 'Keep one port', 'logic', 'local'
    takes, gives = 'endpoint', 'endpoint'
    output_fields = ENDPOINT_FIELDS
    fields = (Field('port', 'Port', 'number', required=True, minimum=1, maximum=65535),)

    def begin(self, ctx, items, settings, allowance):
        items, dropped = refresh(items)          # never act on records hidden since the last step
        kept = [item for item in items if item['fields']['port'] == settings['port']]
        return Done(kept, note=str(len(kept)) + ' kept' + gone(dropped))
```

From this the catalogue row, the settings form, the "Add step" offer (by `takes` and the previous `gives`),
validation and the estimate all follow. Rules:

- `group` is one of `trigger, find, collect, enrich, organize, logic, data, send`; `reach` is one of
  `local, public, credits, target, send`. A start block sets `start = True` and takes nothing.
- Field types: `text`, `number` (`minimum`, `maximum`), `choice` (`choices` as `(value, label)` pairs), `tags`, `conditions`.
- `cost(settings, items_in)` returns `Cost(pages, contacts, items_out)`: the worst case. Declare it for anything
  that spends or contacts, or the confirmation will understate the run.
- A block that starts jobs returns `Waiting(run_ids, state)` from `begin` and finishes in
  `resume(ctx, items, settings, state, runs)`, where `runs` maps each id to its finished `Run`. Respect
  `allowance` (`{"pages", "contacts"}` remaining) and report what did not fit as `left_out`.
  `leakscope/build/blocks/collect.py` is the model.
- Raise `BlockError` for a failure the person should read. Never call a provider or a target directly: queue
  the existing job so its limits apply.
- Tests: copy a case from `tests/test_build_blocks.py` (local) or `tests/test_build_jobs.py` (waiting), patching
  the queue function so no provider or target is contacted.

Conditions need no new block: Filter reads every field of the item.

## Not built yet

Test with saved data, AI blocks (Research, Ask), free placement of boxes on the map (it arranges itself).
