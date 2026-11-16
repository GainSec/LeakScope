"""A paginated read adapter over the service-specific result tables."""

from functools import cmp_to_key
from django.db.models import Q, F, Value, Case, When, CharField
from django.db.models.functions import Cast, Coalesce, Concat, NullIf, Substr
from .table_sorting import ordered
from leakscope.services.registry import canonical_type, model_for_type, service_types
from leakscope.services.identity import fingerprint, normalize_host, normalize_port
from leakscope_app.models import FindingReview, FingerprintLog


def selected_models(type_name=None):
    if type_name:
        model = model_for_type(type_name)
        if model is None:
            raise ValueError("Unsupported service type")
        return {canonical_type(type_name): model}
    return {s["key"]: s["model"] for s in service_types()}


def review_context():
    overrides = {
        (canonical_type(r["type"]), r["object_id"]): r["hidden"]
        for r in FindingReview.objects.values("type", "object_id", "hidden")
    }
    legacy = list(
        FingerprintLog.objects.filter(status="hidden").values(
            "type", "ip", "port", "fingerprint"
        )
    )
    return overrides, legacy, {r["fingerprint"] for r in legacy}


def hidden_condition(key, model, context):
    overrides, legacy, _ = context
    hidden = {pk for (kind, pk), value in overrides.items() if kind == key and value}
    shown = {pk for (kind, pk), value in overrides.items() if kind == key and not value}
    condition = Q(pk__in=hidden)
    fields = {f.name for f in model._meta.fields}
    if "ip" in fields:
        for row in legacy:
            if canonical_type(row["type"]) != key:
                continue
            match = Q(ip=row["ip"])
            if "port" in fields:
                match &= Q(port=str(row["port"] or ""))
            condition |= match & ~Q(pk__in=shown)
    return condition



def summary_counts(models=None):
    """Count every legacy type/provider in one query without loading findings."""
    models = selected_models() if models is None else models
    context = review_context()
    branches = []
    from django.db.models import Count
    for key, model in models.items():
        branches.append(model.objects.order_by()
            .annotate(service_key=Value(key, output_field=CharField()))
            .values('service_key', 'provider')
            .annotate(total=Count('pk'),
                      confirmed_count=Count('pk', filter=Q(confirmed=True)),
                      later_count=Count('pk', filter=Q(for_later=True)),
                      hidden_count=Count('pk', filter=hidden_condition(key, model, context)))
            .values('service_key', 'provider', 'total', 'confirmed_count', 'later_count', 'hidden_count'))
    totals = {'total': 0, 'confirmed': 0, 'for_later': 0, 'hidden': 0}
    by_type, by_provider = {}, {}
    if branches:
        for row in branches[0].union(*branches[1:], all=True):
            key, provider = row['service_key'], row['provider'] or 'unknown'
            by_type[key] = by_type.get(key, 0) + row['total']
            by_provider[provider] = by_provider.get(provider, 0) + row['total']
            for target, source in [('total', 'total'), ('confirmed', 'confirmed_count'),
                                   ('for_later', 'later_count'), ('hidden', 'hidden_count')]:
                totals[target] += row[source]
    return totals, by_type, by_provider


def hidden_for_row(key, obj, context):
    overrides, _, hashes = context
    if (key, obj.pk) in overrides:
        return overrides[key, obj.pk]
    host = getattr(obj, "ip", "")
    if not host:
        return False
    if fingerprint(key, host, getattr(obj, "port", "")) in hashes:
        return True
    return any(
        canonical_type(row["type"]) == key
        and normalize_host(row["ip"]) == normalize_host(host)
        and normalize_port(row["port"]) == normalize_port(getattr(obj, "port", ""))
        for row in context[1]
    )


def page(
    type_name=None, provider="", status="", q="", page=1, page_size=30, serializer=None,
    sort="created_at", direction="desc"
):
    context = review_context()
    total = 0
    rows = []
    limit = page * page_size
    for key, model in selected_models(type_name).items():
        query = model.objects.all()
        fields = {f.name for f in model._meta.fields}
        if "search" in fields:
            query = query.select_related("search")
        if provider:
            query = query.filter(provider=provider)
        if q:
            clause = Q()
            for field in fields & {
                "ip",
                "url",
                "title",
                "name",
                "path",
                "bucket",
                "buckets",
                "keyword",
            }:
                clause |= Q(**{field + "__icontains": q})
            query = query.filter(clause) if clause else query.none()
        hidden = hidden_condition(key, model, context)
        if status == "hidden":
            query = query.filter(hidden)
        elif status != "all":
            query = query.exclude(hidden)
        if status == "confirmed":
            query = query.filter(confirmed=True)
        elif status == "for_later":
            query = query.filter(for_later=True)
        elif status == "new":
            query = query.filter(confirmed=False, for_later=False)
        total += query.count()
        # Every model's top-k must use the same key as the final merged page.
        if sort == 'target':
            host = Coalesce(F('ip'), Value('')) if 'ip' in fields else Value('')
            port = Coalesce(Cast('port', CharField()), Value('')) if 'port' in fields else Value('')
            suffix = Case(When(port='', then=Value('')), default=Concat(Value(':'), port), output_field=CharField()) if 'port' in fields else Value('')
            label = NullIf(Concat(host, suffix), Value(''))
            fallbacks = [NullIf(Cast(name, CharField()), Value('')) for name in ('bucket','path','keyword') if name in fields]
            fallback = Substr(Coalesce(*fallbacks, Concat(Value('Finding '), Cast('pk', CharField()))), 1, 300) if fallbacks else Concat(Value('Finding '), Cast('pk', CharField()))
            expression = Coalesce(label, fallback)
        elif sort == 'type':
            expression = Value(key, output_field=CharField())
        elif sort == 'provider':
            expression = Coalesce(NullIf(F('provider'), Value('')), Value('unknown')) if 'provider' in fields else Value('unknown')
        elif sort == 'status':
            expression = Case(When(hidden, then=Value('hidden')), When(confirmed=True, then=Value('confirmed')),
                              When(for_later=True, then=Value('for_later')), default=Value('new'), output_field=CharField())
        else:
            expression = F('search__created_on') if 'search' in fields else Value(None, output_field=CharField())
        for obj in ordered(query, expression, direction, textual=sort != 'created_at')[:limit]:
            rows.append((obj._table_sort, serializer(obj, key, hidden_for_row(key, obj, context))))

    def compare(a, b):
        av, bv = a[0], b[0]
        if av is None or bv is None:
            primary = (av is None) - (bv is None)
        else:
            primary = ((av > bv) - (av < bv)) * (-1 if direction == 'desc' else 1)
        if primary:
            return primary
        at, bt = (a[1]['type'], a[1]['id']), (b[1]['type'], b[1]['id'])
        return (at > bt) - (at < bt)

    rows.sort(key=cmp_to_key(compare))
    start = (page - 1) * page_size
    return [row for _, row in rows[start : start + page_size]], total
