"""Field conditions for Filter (and later If): picked, never typed as expressions."""
import re

OPS = ('is', 'is_not', 'one_of', 'contains', 'not_contains', 'starts_with', 'ends_with', 'matches', 'not_matches', 'empty', 'not_empty', 'gt', 'lt')
PATTERN_OPS = ('matches', 'not_matches')
# A quantified group that itself contains a quantifier can take exponential time on crafted input.
_NESTED = re.compile(r'\([^()]*[+*][^()]*\)\s*[+*{]')


def pattern(text):
    """Compile a search pattern for matches / does not match, refusing shapes that can run away."""
    if not text or len(text) > 200: raise ValueError('A pattern is 1–200 characters')
    if _NESTED.search(text): raise ValueError('That pattern repeats a repeated group; simplify it')
    try: return re.compile(text, re.IGNORECASE)
    except re.error as exc: raise ValueError('Pattern is not valid: ' + str(exc)) from None


def clean(value):
    if not isinstance(value, dict) or not isinstance(value.get('rules'), list): raise ValueError('Conditions need at least one rule')
    match = value.get('match', 'all')
    if match not in ('all', 'any'): raise ValueError('Conditions match all or any')
    if not 1 <= len(value['rules']) <= 10: raise ValueError('Conditions take 1–10 rules')
    rules = []
    for rule in value['rules']:
        if not isinstance(rule, dict): raise ValueError('Invalid condition')
        field, op, text = rule.get('field'), rule.get('op'), rule.get('value', '')
        if not isinstance(field, str) or not 1 <= len(field.strip()) <= 64: raise ValueError('A condition needs a field')
        if op not in OPS: raise ValueError('Unknown comparison')
        if not isinstance(text, str) or len(text) > 500: raise ValueError('A condition value is text of at most 500 characters')
        if op in PATTERN_OPS: pattern(text.strip())
        rules.append({'field': field.strip(), 'op': op, 'value': text.strip()})
    return {'match': match, 'rules': rules}


def _number(value):
    if isinstance(value, bool): return None
    try: return float(value)
    except (TypeError, ValueError): return None


def _rule(item, rule):
    raw = (item.get('fields') or {}).get(rule['field'])
    values = [str(v).casefold() for v in raw] if isinstance(raw, (list, tuple)) else ([] if raw in (None, '') else [str(raw).casefold()])
    op, wanted = rule['op'], rule['value'].casefold()
    if op == 'empty': return not values
    if op == 'not_empty': return bool(values)
    if op == 'is': return wanted in values
    if op == 'is_not': return wanted not in values
    if op == 'one_of': return bool({part.strip() for part in wanted.split(',') if part.strip()} & set(values))
    if op == 'contains': return any(wanted in value for value in values)
    if op == 'not_contains': return not any(wanted in value for value in values)
    if op == 'starts_with': return any(value.startswith(wanted) for value in values)
    if op == 'ends_with': return any(value.endswith(wanted) for value in values)
    if op in PATTERN_OPS:
        found = any(pattern(rule['value']).search(value[:2000]) for value in values)
        return found if op == 'matches' else not found
    left, right = _number(raw), _number(rule['value'])
    if left is None or right is None: return False
    return left > right if op == 'gt' else left < right


def matches(item, cleaned):
    results = (_rule(item, rule) for rule in cleaned['rules'])
    return all(results) if cleaned['match'] == 'all' else any(results)
