import logging
import re
from typing import Any, Dict, Optional

logger=logging.getLogger(__name__)

def build_shodan_query(base_query: str, keyword: Optional[str] = None, country: Optional[str] = None,
                       network: Optional[str] = None, exclude: Optional[str] = None) -> str:
    parts = [base_query] if base_query else []

    if keyword:
        keyword = keyword.strip()
        if keyword:
            if any(character.isspace() or character in '"\\' for character in keyword):
                parts.append(_quote(keyword))
            else:
                parts.append(keyword)

    if country:
        parts.append(f"country:{country}")

    if network:
        network = network.strip()
        if network:
            if '/' in network:
                parts.append(f"net:{network}")
            else:
                parts.append(f"ip:{network}")

    if exclude:
        tokens = [t.strip() for t in exclude.split(',') if t.strip()]
        for token in tokens:
            if token.startswith('-'):
                parts.append(token)
            elif any(character.isspace() or character in '"\\' for character in token):
                parts.append('-' + _quote(token))
            else:
                parts.append(f"-{token}")

    return " ".join(parts).strip()


def _quote(value):
    return '"' + str(value).replace('\\', '\\\\').replace('"', '\\"') + '"'


def _zoomeye_expression(base_query):
    # Built-in legacy presets use colon/OR syntax; convert only their lexical
    # fields. User-authored custom queries bypass this builder entirely.
    tokens = re.findall(r'[^\s"\']*(?:"[^"\\]*(?:\\.[^"\\]*)*"|\'[^\']*\')[^\s]*|[^\s]+', base_query)
    result = []
    operators = {'OR': '||', 'AND': '&&', '||': '||', '&&': '&&'}
    for token in tokens:
        token = operators.get(token, token)
        token = re.sub(r'^([a-zA-Z_.]+):', r'\1=', token)
        if result and result[-1] not in ('||', '&&') and token not in ('||', '&&'):
            result.append('&&')
        result.append(token)
    return ' '.join(result)


def build_provider_query(provider: str, base_query: str, keyword: Optional[str] = None,
                         country: Optional[str] = None, network: Optional[str] = None,
                         exclude: Optional[str] = None) -> str:
    if (provider or '').lower() != 'zoomeye':
        return build_shodan_query(base_query, keyword=keyword, country=country, network=network, exclude=exclude)
    clauses = []
    if base_query.strip():
        clauses.append('(' + _zoomeye_expression(base_query.strip()) + ')')
    if keyword and keyword.strip():
        clauses.append(_quote(keyword.strip()))
    if country and country.strip():
        clauses.append('country=' + _quote(country.strip()))
    if network and network.strip():
        field = 'cidr' if '/' in network else 'ip'
        clauses.append(field + '=' + _quote(network.strip()))
    if exclude:
        for value in exclude.split(','):
            value = value.strip().lstrip('-')
            if value:
                clauses.append('body!=' + _quote(value))
    return ' && '.join(clauses)


class _SafeDict(dict):
    def __missing__(self, key):
        return ""


def render_query_template(template: str, params: Dict[str, Any]) -> str:
    try:
        return template.format_map(_SafeDict(**params))
    except Exception:
        logger.exception("Failed to render custom query template")
        return template

