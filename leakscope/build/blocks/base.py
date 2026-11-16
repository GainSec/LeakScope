"""The block definition format. A block is one class; the palette, settings form, connection rules,
cost estimate and run record are all derived from its attributes."""
import re
from dataclasses import dataclass, field as dataclass_field, asdict

REACHES = ('local', 'public', 'credits', 'target', 'send')
GROUPS = ('trigger', 'find', 'collect', 'enrich', 'organize', 'logic', 'data', 'send')
KINDS = ('endpoint', 'domain', 'value')
FIELD_TYPES = ('text', 'number', 'choice', 'tags', 'conditions', 'flag', 'list')


@dataclass(frozen=True)
class Field:
    key: str
    label: str
    type: str
    required: bool = False
    default: object = None
    choices: tuple = ()
    minimum: int = None
    maximum: int = None
    help: str = ''
    options: str = ''       # for a choice: a named list supplied by the API (organizations)
    suggest: str = ''       # for text: a named list offered as suggestions (technologies, tags)
    template: bool = False  # text or tags that may contain {{name}} values from earlier steps


@dataclass(frozen=True)
class Cost:
    """Worst case for one step. items_out None means the same number as came in."""
    pages: int = 0
    contacts: int = 0
    items_out: int = None


@dataclass
class Done:
    items: list
    pages: int = 0
    contacts: int = 0
    note: str = ''
    left_out: int = 0
    branches: dict = None   # for a block with sides (If): the items for each side


@dataclass
class Waiting:
    run_ids: list
    state: dict = dataclass_field(default_factory=dict)


@dataclass(frozen=True)
class Context:
    workflow_id: str
    workflow_run_id: str
    workflow_name: str
    origin: str
    step_id: str = ''
    since: str = ''         # for an automatic run: only records newer than this moment


class BlockError(ValueError):
    """A step failed; the message is safe to show."""


class Block:
    key = name = group = ''
    says = ''               # one plain sentence shown in the block library
    reach = 'local'
    start = False
    takes = None            # None for a start block, a kind, a tuple of kinds, or 'any'
    gives = 'endpoint'      # a kind, or 'same' to pass on whatever kind came in
    fields = ()
    output_fields = ()
    names_setting = ''      # the setting that holds the name of a value this block adds to each result
    adds_fixed = ()         # names of values this block always adds
    event = False           # a start block that fires when new records appear
    ports = ()              # named sides a later step chooses between, such as ('yes', 'no')

    def pending(self, settings, since):
        """For an event start: how many new records exist since that moment."""
        return 0

    def clean(self, settings):
        if not isinstance(settings, dict): raise ValueError('Settings must be an object')
        known = {item.key for item in self.fields}
        if set(settings) - known: raise ValueError('Unknown setting: ' + ', '.join(sorted(str(key) for key in set(settings) - known)))
        return {item.key: _clean_field(item, settings.get(item.key)) for item in self.fields}

    def cost(self, settings, items_in):
        return Cost()

    def reaches(self, settings):
        """Every reach this step can have. More than its own for a step that runs something else."""
        return (self.reach,)

    def signature(self, settings):
        """Text that changes when something this step depends on changes outside the workflow. Part of the confirmation token."""
        return ''

    def adds(self, settings):
        """Names of values this step puts on each item, for later steps to use."""
        return list(self.adds_fixed)

    def begin(self, ctx, items, settings, allowance):
        raise NotImplementedError

    def resume(self, ctx, items, settings, state, runs):
        raise NotImplementedError


def _clean_field(item, value):
    if item.type == 'flag':
        if value in (None, ''): return bool(item.default)
        if not isinstance(value, bool): raise ValueError(item.label + ' is ticked or not')
        return value
    missing = value is None or value == '' or value == []
    if item.type == 'conditions':
        if missing and not item.required: return None
        from leakscope.build import conditions
        return conditions.clean(value)
    if item.type == 'list':
        # Addresses or names, one per line: up to 500, each once, none containing a space.
        if isinstance(value, str): value = [part for part in re.split(r'[\n,]+', value)]
        if missing or not isinstance(value, list) or not [part for part in value if isinstance(part, str) and part.strip()]:
            if item.required or not (missing or isinstance(value, list)): raise ValueError(item.label + ' is required' if missing or isinstance(value, list) else item.label + ' must be a list')
            return []
        entries = []
        for part in value:
            if not isinstance(part, str): raise ValueError(item.label + ' must be a list of text entries')
            part = part.strip()
            if not part: continue
            if len(part) > 253 or any(c.isspace() or ord(c) < 32 for c in part): raise ValueError(item.label + ': each entry is at most 253 characters with no spaces')
            if part not in entries: entries.append(part)
        if len(entries) > 500: raise ValueError(item.label + ' takes up to 500 entries')
        return entries
    if item.type == 'tags':
        if missing:
            if item.required: raise ValueError(item.label + ' is required')
            return []
        if not isinstance(value, list) or len(value) > 20: raise ValueError(item.label + ' takes up to 20 tags')
        names = []
        for name in value:
            if not isinstance(name, str) or not 1 <= len(name.strip()) <= 64 or any(ord(c) < 32 for c in name): raise ValueError(item.label + ' must be 1–64 printable characters each')
            names.append(name.strip())
        return names
    if missing:
        if item.required: raise ValueError(item.label + ' is required')
        return item.default if item.default is not None else ('' if item.type == 'text' else None)
    if item.type == 'text':
        if not isinstance(value, str): raise ValueError(item.label + ' must be text')
        value = value.strip()
        if len(value) > 8000: raise ValueError(item.label + ' exceeds 8000 characters')
        if not value and item.required: raise ValueError(item.label + ' is required')
        return value
    if item.type == 'number':
        if isinstance(value, bool) or not isinstance(value, (int, str)): raise ValueError(item.label + ' must be a whole number')
        try: number = int(value)
        except ValueError: raise ValueError(item.label + ' must be a whole number') from None
        if (item.minimum is not None and number < item.minimum) or (item.maximum is not None and number > item.maximum):
            raise ValueError(item.label + ' must be between ' + str(item.minimum) + ' and ' + str(item.maximum))
        return number
    if item.options:
        if not isinstance(value, str) or len(value) > 64: raise ValueError(item.label + ' is not one of the choices')
        return value
    if value not in [choice[0] for choice in item.choices]: raise ValueError(item.label + ' is not one of the choices')
    return value


_REGISTRY = {}


def register(cls):
    if not isinstance(cls.key, str) or not cls.key or not cls.name: raise ValueError('A block needs a key and a name')
    if cls.key in _REGISTRY: raise ValueError('Block key already registered: ' + cls.key)
    if cls.reach not in REACHES: raise ValueError(cls.key + ': unknown reach')
    if cls.group not in GROUPS: raise ValueError(cls.key + ': unknown group')
    if cls.start and cls.takes is not None: raise ValueError(cls.key + ': a start block takes nothing')
    if not cls.start and cls.takes != 'any' and not (accepted(cls) and all(kind in KINDS for kind in accepted(cls))): raise ValueError(cls.key + ': unknown input kind')
    if cls.gives not in KINDS + ('same',) or (cls.gives == 'same' and cls.start): raise ValueError(cls.key + ': unknown output kind')
    for item in cls.fields:
        if item.type not in FIELD_TYPES: raise ValueError(cls.key + ': unknown field type')
    _REGISTRY[cls.key] = cls()
    return cls


def accepted(block):
    """The kinds a block takes, as a tuple (empty for a start block or 'any')."""
    if block.takes in (None, 'any'): return ()
    return (block.takes,) if isinstance(block.takes, str) else tuple(block.takes)


def fits(block, kind):
    return block.takes == 'any' or kind in accepted(block)


def unregister(key):
    _REGISTRY.pop(key, None)


def get(key):
    return _REGISTRY[key]


def catalog():
    rows = []
    for block in sorted(_REGISTRY.values(), key=lambda b: (GROUPS.index(b.group), b.name)):
        fields = [{**asdict(item), 'choices': [list(choice) for choice in item.choices]} for item in block.fields]
        rows.append({'key': block.key, 'name': block.name, 'says': block.says, 'group': block.group, 'reach': block.reach, 'start': block.start, 'event': block.event,
                     'takes': block.takes if block.takes in (None, 'any') else list(accepted(block)), 'gives': block.gives, 'ports': list(block.ports), 'names_setting': block.names_setting, 'adds_fixed': list(block.adds_fixed), 'fields': fields, 'output_fields': list(block.output_fields)})
    return rows
