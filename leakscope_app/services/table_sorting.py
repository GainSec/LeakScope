"""Allowlisted ordering for saved-data tables, applied before pagination."""
from django.db import connection
from django.db.models import F, Value
from django.db.models.functions import Collate, Lower, NullIf


def parameters(request, allowed, default, default_direction='desc'):
    key = request.GET.get('sort', default)
    direction = request.GET.get('direction', default_direction)
    if key not in allowed:
        raise ValueError('Unknown sort column')
    if direction not in {'asc', 'desc'}:
        raise ValueError('Unknown sort direction')
    return key, direction


def text_value(expression):
    # Byte/codepoint ordering lets the legacy multi-model merge reproduce SQL
    # ordering exactly, independent of the deployment's locale collation.
    return Collate(Lower(NullIf(expression, Value(''))),
                   'C' if connection.vendor == 'postgresql' else 'BINARY')


def ordered(query, expression, direction, textual=False, ties=('pk',)):
    if textual:
        expression = text_value(expression)
    query = query.annotate(_table_sort=expression)
    order = F('_table_sort').desc(nulls_last=True) if direction == 'desc' else F('_table_sort').asc(nulls_last=True)
    return query.order_by(order, *ties)
