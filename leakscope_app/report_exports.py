"""Downloads built from saved data only: a screenshots archive, an organization report and all matching certificates.

Each one reuses the page's own listing view, so a download holds what the page shows for the same filters.
Nothing here contacts a provider or a target.
"""
import copy
import csv
import io
import json
import re
import tempfile
import zipfile
from html import escape

from django.core.exceptions import ObjectDoesNotExist
from django.http import FileResponse, HttpResponse, QueryDict
from django.utils import timezone

from .workbench import endpoint

IMAGE_TYPES = {"image/png": "png", "image/jpeg": "jpg"}
ARCHIVE_BYTES = 2 * 1024 ** 3


def _number(value, default, maximum):
    if value in (None, ""):
        return default
    try:
        number = int(value)
    except (TypeError, ValueError):
        raise ValueError("Invalid limit") from None
    if not 1 <= number <= maximum:
        raise ValueError("Limit outside supported range")
    return number


def _call(view, request, params, **kwargs):
    """Run one of the page's own GET views with other parameters and return its data."""
    clone = copy.copy(request)
    query = QueryDict(mutable=True)
    for key, value in params.items():
        query[key] = value
    clone.GET = query
    clone.method = "GET"
    response = view(clone, **kwargs)
    data = json.loads(response.content)
    if response.status_code == 404:
        raise ObjectDoesNotExist(data.get("error") or "Record not found")
    if response.status_code != 200:
        raise ValueError(data.get("error") or "Export failed")
    return data


def _slug(value, fallback="export"):
    return re.sub(r"[^a-z0-9]+", "-", str(value or "").lower()).strip("-")[:60] or fallback


def _stamp():
    return timezone.localtime().strftime("%Y-%m-%d")


def _cell(value):
    if value is None:
        return ""
    if isinstance(value, bool):
        return "yes" if value else "no"
    if isinstance(value, (list, tuple)):
        return "; ".join(_cell(part) for part in value)
    if isinstance(value, dict):
        return json.dumps(value, sort_keys=True, default=str)
    return re.sub(r"\s+", " ", str(value)).strip()


def _defuse(value):
    # A spreadsheet would run a cell starting with these as a formula.
    return "'" + value if value.startswith(("=", "+", "-", "@", "\t", "\r")) else value


def _csv(blocks):
    """blocks: [(title or None, columns, rows)] — one header and its rows per block, blank line between."""
    buffer = io.StringIO()
    writer = csv.writer(buffer, lineterminator="\r\n")
    for index, (title, columns, rows) in enumerate(blocks):
        if index:
            writer.writerow([])
        if title:
            writer.writerow([_defuse(title)])
        writer.writerow(columns)
        for row in rows:
            writer.writerow([_defuse(_cell(value)) for value in row])
    return "﻿" + buffer.getvalue()


def _text(blocks, heading=None):
    lines = []
    if heading:
        lines += [heading, "=" * len(heading), ""]
    for title, columns, rows in blocks:
        if title:
            lines += [title, "-" * len(title)]
        if columns == ["Field", "Value"]:
            width = max((len(_cell(row[0])) for row in rows), default=0)
            lines += [_cell(row[0]).ljust(width) + "  " + _cell(row[1]) for row in rows]
        else:
            lines.append("\t".join(columns))
            lines += ["\t".join(_cell(value) for value in row) for row in rows]
        if not rows:
            lines.append("(none)")
        lines.append("")
    return "\n".join(lines)


def _download(content, name, kind):
    response = HttpResponse(content, content_type=kind + "; charset=utf-8")
    response["Content-Disposition"] = 'attachment; filename="%s"' % name
    response["X-Content-Type-Options"] = "nosniff"
    return response


STYLE = """
:root{color-scheme:light dark;--ink:#16202b;--soft:#5b6875;--line:#d9e0e7;--page:#f5f7f9;--card:#fff;--accent:#0b7a75;--head:#eef2f5}
@media(prefers-color-scheme:dark){:root{--ink:#e6edf3;--soft:#9aa7b4;--line:#2a3541;--page:#0d1319;--card:#141c24;--accent:#5fd4c8;--head:#1a242e}}
*{box-sizing:border-box}body{margin:0;background:var(--page);color:var(--ink);font:15px/1.5 system-ui,-apple-system,"Segoe UI",sans-serif}
main{max-width:1100px;margin:0 auto;padding:32px 20px 56px}
header.report{border-bottom:3px solid var(--accent);padding-bottom:14px;margin-bottom:22px}
header.report h1{margin:0;font-size:1.9rem;line-height:1.2;overflow-wrap:anywhere}
header.report p{margin:4px 0 0;color:var(--soft)}
.tiles{display:grid;grid-template-columns:repeat(auto-fit,minmax(130px,1fr));gap:10px;margin:0 0 22px}
.tile{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:12px 14px}
.tile b{display:block;font-size:1.5rem;line-height:1.1}.tile span{color:var(--soft);font-size:.82rem}
section{background:var(--card);border:1px solid var(--line);border-radius:10px;margin:0 0 18px;overflow:hidden;break-inside:avoid}
section h2{margin:0;padding:11px 16px;font-size:1.02rem;background:var(--head);border-bottom:1px solid var(--line)}
section h2 small{font-weight:400;color:var(--soft);margin-left:8px}
table{width:100%;border-collapse:collapse;table-layout:auto}
th,td{text-align:left;vertical-align:top;padding:7px 16px;border-bottom:1px solid var(--line);overflow-wrap:anywhere;min-width:13ch}
th{font-size:.78rem;color:var(--soft);font-weight:600}tr:last-child td{border-bottom:0}
table.facts th{width:210px;font-size:.88rem}
p.none,p.note{margin:0;padding:12px 16px;color:var(--soft)}
.shots{display:grid;grid-template-columns:repeat(auto-fill,minmax(320px,1fr));gap:16px}
.shot{background:var(--card);border:1px solid var(--line);border-radius:10px;overflow:hidden;break-inside:avoid}
.shot a.image{display:block;background:#000}.shot img{display:block;width:100%;height:auto}
.shot h2{margin:0;padding:10px 14px 2px;font-size:1rem;overflow-wrap:anywhere}.shot p.sub{margin:0;padding:0 14px 8px;color:var(--soft);font-size:.86rem;overflow-wrap:anywhere}
.shot th,.shot td{padding:4px 14px;font-size:.84rem}.shot th{width:108px}
footer{color:var(--soft);font-size:.82rem;margin-top:26px}
@media print{body{background:#fff}main{max-width:none;padding:0}section,.shot,.tile{border-color:#bbb}}
"""


ISO = re.compile(r"^(\d{4}-\d\d-\d\d)T(\d\d:\d\d):\d\d(?:\.\d+)?(?:\+00:00|Z)$")


def _shown(value):
    """A cell as a person reads it on the page: a saved UTC timestamp without its seconds."""
    text = _cell(value)
    found = ISO.match(text)
    return escape("%s %s UTC" % found.groups() if found else text)


def _page(title, subtitle, body, note):
    return ("<!doctype html><html lang=\"en\"><head><meta charset=\"utf-8\"><meta name=\"viewport\" content=\"width=device-width,initial-scale=1\">"
            "<title>%s</title><style>%s</style></head><body><main><header class=\"report\"><h1>%s</h1><p>%s</p></header>%s"
            "<footer>%s</footer></main></body></html>") % (escape(title), STYLE, escape(title), escape(subtitle), body, escape(note))


def _section(title, columns, rows):
    count = "" if columns == ["Field", "Value"] else "<small>%d</small>" % len(rows)
    if not rows:
        return "<section><h2>%s%s</h2><p class=\"none\">None saved.</p></section>" % (escape(title), count)
    if columns == ["Field", "Value"]:
        inner = "".join("<tr><th>%s</th><td>%s</td></tr>" % (escape(_cell(a)), _shown(b)) for a, b in rows)
        return "<section><h2>%s</h2><table class=\"facts\">%s</table></section>" % (escape(title), inner)
    head = "".join("<th>%s</th>" % escape(name) for name in columns)
    inner = "".join("<tr>%s</tr>" % "".join("<td>%s</td>" % _shown(value) for value in row) for row in rows)
    return "<section><h2>%s%s</h2><table><thead><tr>%s</tr></thead><tbody>%s</tbody></table></section>" % (escape(title), count, head, inner)


# ---- certificates -----------------------------------------------------------------------------------------------

@endpoint()
def certificates(request):
    """Every saved certificate matching the page's search, not only the page shown."""
    from . import relationships as assets
    fmt = request.GET.get("format", "csv").lower()
    if fmt not in {"csv", "txt"}:
        raise ValueError("Select CSV or TXT")
    limit = _number(request.GET.get("limit"), 20000, 20000)
    params = {key: request.GET[key] for key in ("q", "mode", "watch", "since", "until", "sort", "direction") if request.GET.get(key)}
    rows, page, total = [], 1, 0
    while len(rows) < limit:
        data = _call(assets.certificates, request, {**params, "page": str(page), "page_size": "100"})
        total = data.get("count", 0)
        for item in data.get("results", []):
            issuer = item.get("issuer") or {}
            rows.append([item.get("fingerprint") or item.get("key") or item.get("id"), item.get("domains") or [],
                         (issuer.get("O") or issuer.get("CN") or "") if isinstance(issuer, dict) else issuer, item.get("serial"),
                         item.get("not_before"), item.get("not_after"), item.get("first_seen"), item.get("last_seen"),
                         [match.get("watch_name") or match.get("watch_id") for match in item.get("watch_matches") or []], item.get("id")])
        if page >= data.get("pages", 1) or not data.get("results"):
            break
        page += 1
    rows = rows[:limit]
    columns = ["Certificate", "Names", "Issuer", "Serial", "Valid from", "Valid until", "First seen", "Last seen", "Watch matches", "ID"]
    blocks = [(None, columns, rows)]
    name = "certificates-%s.%s" % (_stamp(), fmt)
    response = _download(_csv(blocks), name, "text/csv") if fmt == "csv" else _download(_text(blocks), name, "text/plain")
    response["X-Total-Count"] = str(total)
    response["X-Exported-Count"] = str(len(rows))
    return response


# ---- any paged results table ------------------------------------------------------------------------------------

TABLES = {  # name: (module, view, largest page the view allows)
    "observations": ("leakscope_app.operations", "endpoints", 100),
    "findings": ("leakscope_app.workbench", "findings", 200),
    "cloud": ("leakscope_app.cloud", "resources", 100),
}


def _flat(record):
    """One saved record as a flat row: plain values kept, one level of nesting spread into "name.part" columns."""
    row = {}
    for key, value in record.items():
        if isinstance(value, dict):
            for part, inner in value.items():
                if not isinstance(inner, (dict, list)):
                    row[key + "." + part] = inner
        elif isinstance(value, list) and any(isinstance(item, (dict, list)) for item in value):
            row[key] = len(value)
        else:
            row[key] = value
    return row


@endpoint()
def table(request, source):
    """Every row matching a results page's filters, with the fields the page's own listing returns."""
    from importlib import import_module
    if source not in TABLES:
        raise ObjectDoesNotExist("Unknown table")
    module, view, size = TABLES[source]
    fmt = request.GET.get("format", "csv").lower()
    if fmt not in {"csv", "txt"}:
        raise ValueError("Select CSV or TXT")
    limit = _number(request.GET.get("limit"), 20000, 20000)
    params = {key: value for key, value in request.GET.items() if key not in {"format", "limit", "page", "page_size"} and value}
    records, page, total = [], 1, 0
    while len(records) < limit:
        data = _call(getattr(import_module(module), view), request, {**params, "page": str(page), "page_size": str(size)})
        found = data.get("results") or []
        total = data.get("count", len(found))
        records += [_flat(item) for item in found if isinstance(item, dict)]
        if not found or len(records) >= total or page >= data.get("pages", page + 1):
            break
        page += 1
    records = records[:limit]
    columns = list(dict.fromkeys(key for record in records for key in record))
    blocks = [(None, columns or ["No rows"], [[record.get(key) for key in columns] for record in records])]
    name = "%s-%s.%s" % (source, _stamp(), fmt)
    response = _download(_csv(blocks), name, "text/csv") if fmt == "csv" else _download(_text(blocks), name, "text/plain")
    response["X-Total-Count"] = str(total)
    response["X-Exported-Count"] = str(len(records))
    return response


# ---- job history ------------------------------------------------------------------------------------------------

@endpoint()
def runs(request):
    """Every job in the history, newest first; the page itself lists only the newest hundred."""
    from .models import Run
    fmt = request.GET.get("format", "csv").lower()
    if fmt not in {"csv", "txt"}:
        raise ValueError("Select CSV or TXT")
    limit = _number(request.GET.get("limit"), 20000, 20000)
    total = Run.objects.count()
    fields = ("kind", "type", "provider", "state", "created_at", "started_at", "finished_at", "progress_current", "progress_total", "error", "pk")
    rows = []
    for row in Run.objects.order_by("-created_at", "pk").values_list(*fields)[:limit].iterator(chunk_size=2000):
        kind, type_name, provider, state, created, started, finished, done, whole, error, key = row
        rows.append([kind, type_name, provider or "direct", state, created.isoformat() if created else "", started.isoformat() if started else "",
                     finished.isoformat() if finished else "", "%d of %d" % (done, whole) if whole else "", (error or "")[:500], str(key)])
    blocks = [(None, ["Operation", "Type", "Provider", "State", "Created", "Started", "Finished", "Progress", "Error", "Run ID"], rows)]
    name = "job-history-%s.%s" % (_stamp(), fmt)
    response = _download(_csv(blocks), name, "text/csv") if fmt == "csv" else _download(_text(blocks), name, "text/plain")
    response["X-Total-Count"] = str(total)
    response["X-Exported-Count"] = str(len(rows))
    return response


# ---- organization report ----------------------------------------------------------------------------------------

def _organization_blocks(data):
    from leakscope.phase3.state import visible_endpoints
    org = data["organization"]
    overview = data.get("overview") or {}
    counts = overview.get("counts") or {}
    facts = [["Name", org.get("label") or org.get("value")], ["Status", org.get("status") or "active"],
             ["First saved", org.get("first_seen")], ["Last updated", org.get("last_seen")]]
    for key, value in sorted((org.get("attributes") or {}).items()):
        if value in (None, "", [], {}):
            continue
        facts.append([key.replace("_", " ").capitalize(), value])
    members = data.get("members") or []
    endpoint_ids = [member["attributes"].get("endpoint_id") for member in members
                    if member.get("kind") == "service" and isinstance(member.get("attributes"), dict) and member["attributes"].get("endpoint_id")]
    services = []
    for start in range(0, len(endpoint_ids), 500):
        try:
            found = visible_endpoints().filter(pk__in=endpoint_ids[start:start + 500]).order_by("address", "port")
            services += [[row.address, row.port, row.transport, row.service_type, row.title, row.first_seen.isoformat(), row.last_seen.isoformat()] for row in found]
        except (ValueError, TypeError):
            pass  # a legacy member with an unreadable endpoint reference
    blocks = [
        ("Organization", ["Field", "Value"], facts),
        ("Totals", ["Field", "Value"], [[key.replace("_", " ").capitalize(), value] for key, value in counts.items()]),
        ("Domain scopes", ["Domain", "Source"], [[row.get("name"), row.get("source")] for row in data.get("domains") or []]),
        ("Saved results", ["Kind", "Result", "Relationship", "Source link", "First seen", "Last seen"],
         [[row.get("kind"), row.get("label") or row.get("value"), row.get("relationship") or "user scope", row.get("url"), row.get("first_seen"), row.get("last_seen")] for row in members]),
        ("Services", ["Address", "Port", "Transport", "Technology", "Title", "First seen", "Last seen"], services),
        ("Technologies", ["Technology", "Services"], [[row.get("technology"), row.get("count")] for row in overview.get("technologies") or []]),
        ("Result kinds", ["Kind", "Count"], [[row.get("kind"), row.get("count")] for row in overview.get("kinds") or []]),
        ("Evidence sources", ["Source", "Records"], [[row.get("source"), row.get("count")] for row in overview.get("sources") or []]),
        ("Recent evidence", ["Subject", "Kind", "Source", "Observed", "Collected"],
         [[row.get("subject"), row.get("subject_kind"), row.get("source"), row.get("observed_at"), row.get("collected_at")] for row in overview.get("recent_evidence") or []]),
    ]
    return blocks, counts, overview.get("basis") or ""


@endpoint()
def organization(request, organization_id):
    """Everything saved about one organization as a formatted report, plain text, CSV or JSON."""
    from . import relationships as assets
    fmt = request.GET.get("format", "html").lower()
    if fmt not in {"html", "txt", "csv", "json"}:
        raise ValueError("Select HTML, TXT, CSV or JSON")
    data = _call(assets.organization_detail, request, {}, organization_id=organization_id)
    name = data["organization"].get("label") or data["organization"].get("value") or "organization"
    filename = "organization-%s-%s.%s" % (_slug(name, "report"), _stamp(), fmt)
    if fmt == "json":
        return _download(json.dumps(data, indent=2, sort_keys=True, default=str), filename, "application/json")
    blocks, counts, basis = _organization_blocks(data)
    made = "Exported from LeakScope on " + timezone.localtime().strftime("%Y-%m-%d %H:%M %Z")
    if fmt == "csv":
        return _download(_csv(blocks), filename, "text/csv")
    if fmt == "txt":
        return _download(_text(blocks, name) + made + "\n" + basis + "\n", filename, "text/plain")
    tiles = "".join("<div class=\"tile\"><b>%s</b><span>%s</span></div>" % (escape(_cell(value)), escape(key.replace("_", " ").capitalize())) for key, value in counts.items())
    body = ("<div class=\"tiles\">%s</div>" % tiles if tiles else "") + "".join(_section(*block) for block in blocks if block[0] != "Totals")
    return _download(_page(name, "Organization report · " + made, body, basis), filename, "text/html")


# ---- screenshots archive ----------------------------------------------------------------------------------------

SHOT_COLUMNS = ["File", "Address", "Port", "Title", "Technology", "Image type", "Provider", "Country", "Organizations", "Tags",
                "Collections", "Captured", "Width", "Height", "Bytes", "SHA-256", "Screenshot ID", "Endpoint ID"]


def _shot_row(item, file):
    return [file, item.get("address"), item.get("port"), item.get("title"), item.get("technology"), item.get("capture_label"),
            item.get("provider"), "" if item.get("country") == "unknown" else item.get("country"),
            [row.get("name") for row in item.get("organizations") or []], item.get("tags") or [],
            [row.get("name") for row in item.get("collections") or []], item.get("created_at"), item.get("width"), item.get("height"),
            item.get("size"), item.get("sha256"), item.get("id"), item.get("endpoint_id")]


def _shot_card(row):
    file, title = row[0], "%s:%s" % (row[1], row[2])
    image = ("<a class=\"image\" href=\"%s\"><img loading=\"lazy\" src=\"%s\" alt=\"Saved image for %s\"></a>" % (escape(file), escape(file), escape(title))
             if file else "<p class=\"none\">The image file is no longer stored.</p>")
    facts = "".join("<tr><th>%s</th><td>%s</td></tr>" % (escape(name), _shown(value))
                    for name, value in list(zip(SHOT_COLUMNS, row))[4:16] if _cell(value))
    return "<article class=\"shot\">%s<h2>%s</h2><p class=\"sub\">%s</p><table>%s</table></article>" % (image, escape(title), escape(_cell(row[3])), facts)


@endpoint(("GET", "POST"))
def screenshots(request):
    """A ZIP of the saved images matching the gallery's filters, with a CSV of their fields and an HTML page showing both."""
    from . import gallery
    from .models import EvidenceArtifact
    from leakscope.phase3.jobs import capture_root
    # POST carries the chosen images (ids) with the filters they were chosen under; GET takes every match of the filters.
    given = request.POST if request.method == "POST" else request.GET
    limit = _number(given.get("limit"), 500, 2000)
    params = {key: value for key, value in given.items() if key in gallery.FILTERS and value}
    chosen = None
    if request.method == "POST":
        chosen = {value.strip().lower() for value in given.get("ids", "").split(",") if value.strip()}
        if not chosen or len(chosen) > 500 or not all(re.fullmatch(r"[0-9a-f-]{32,36}", value) for value in chosen):
            raise ValueError("Choose between 1 and 500 screenshots")
    items, cursor, total = [], None, 0
    while len(items) < (len(chosen) if chosen else limit):
        data = _call(gallery.screenshots, request, {"cursor": cursor, "limit": "100"} if cursor else {**params, "limit": "100"})
        if not cursor:
            total = len(chosen) if chosen else data.get("count", 0)
        items += [item for item in data.get("results", []) if not chosen or item["id"].lower() in chosen]
        cursor = data.get("next_cursor")
        if not cursor:
            break
    items = items[:limit]
    root = capture_root()
    paths = dict(EvidenceArtifact.objects.filter(pk__in=[item["id"] for item in items]).values_list("pk", "relative_path"))
    archive = tempfile.TemporaryFile()
    rows, written, missing, skipped = [], 0, 0, 0
    with zipfile.ZipFile(archive, "w", zipfile.ZIP_STORED, allowZip64=True) as bundle:
        for index, item in enumerate(items, 1):
            file = ""
            relative = next((value for key, value in paths.items() if str(key) == item["id"]), None)
            path = (root / relative).resolve() if relative else None
            if not path or not path.is_relative_to(root) or not path.is_file():
                missing += 1
            elif written + path.stat().st_size > ARCHIVE_BYTES:
                skipped += 1
            else:
                extension = IMAGE_TYPES.get(item.get("mime"), path.suffix.lstrip(".").lower() or "bin")
                file = "images/%04d_%s_%s_%s.%s" % (index, _slug(item.get("address"), "host"), item.get("port"), _slug((item.get("created_at") or "")[:19], "undated"), extension)
                bundle.write(path, file)
                written += path.stat().st_size
            rows.append(_shot_row(item, file))
        made = "Exported from LeakScope on " + timezone.localtime().strftime("%Y-%m-%d %H:%M %Z")
        summary = "%d of %d matching images" % (len(items), total)
        if missing:
            summary += " · %d no longer stored" % missing
        if skipped:
            summary += " · %d left out to keep the archive under 2 GB" % skipped
        bundle.writestr("screenshots.csv", _csv([(None, SHOT_COLUMNS, rows)]), zipfile.ZIP_DEFLATED)
        bundle.writestr("index.html", _page("Saved screenshots", summary + " · " + made, "<div class=\"shots\">%s</div>" % "".join(_shot_card(row) for row in rows),
                                             "An image shows what was saved at its capture time. It does not confirm the service is reachable now."), zipfile.ZIP_DEFLATED)
    size = archive.tell()
    archive.seek(0)
    response = FileResponse(archive, as_attachment=True, filename="screenshots-%s.zip" % _stamp(), content_type="application/zip")
    response["Content-Length"] = str(size)
    response["X-Content-Type-Options"] = "nosniff"
    response["X-Total-Count"] = str(total)
    response["X-Exported-Count"] = str(len(items) - missing - skipped)
    return response
