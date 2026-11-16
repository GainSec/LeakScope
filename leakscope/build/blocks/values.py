"""Values: name a piece of a result so later steps can use it as {{name}}."""
from leakscope.build import conditions
from leakscope.build.kinds import ALL_FIELDS, NAME, render, text_of
from .base import Block, Field, Done, register


def _name(value):
    if not NAME.fullmatch(value): raise ValueError('Name is lowercase letters, digits and underscores, starting with a letter')
    if value in ALL_FIELDS: raise ValueError('Name "' + value + '" is already a field')
    return value


@register
class TakeValue(Block):
    key, name, group, reach, takes, gives = 'take_value', 'Take a value', 'data', 'local', ('endpoint', 'domain'), 'same'
    says = 'Names a field of each result, or a piece of it, so later steps can use it as {{name}}.'
    fields = (Field('name', 'Name', 'text', required=True, help='For example url. Later steps use it as {{url}}.'),
              Field('source', 'Take from', 'text', required=True, suggest='fields'),
              Field('pattern', 'Only the part matching', 'text', help='Optional pattern. With a group in brackets, the group is taken.'),
              Field('missing', 'If it is empty', 'choice', default='skip', choices=(('skip', 'Drop the result'), ('keep', 'Keep the result without the value'))))

    def clean(self, settings):
        cleaned = super().clean(settings)
        _name(cleaned['name'])
        if cleaned['pattern']: conditions.pattern(cleaned['pattern'])
        return cleaned

    names_setting = 'name'

    def adds(self, settings):
        return [settings['name']]

    def begin(self, ctx, items, settings, allowance):
        kept, empty = [], 0
        search = conditions.pattern(settings['pattern']) if settings['pattern'] else None
        for item in items:
            value = text_of((item.get('fields') or {}).get(settings['source']))
            if search and value:
                found = search.search(value[:2000])
                value = '' if not found else (found.group(1) if found.groups() else found.group(0)) or ''
            if not value:
                empty += 1
                if settings['missing'] == 'skip': continue
            kept.append({**item, 'fields': {**(item.get('fields') or {}), settings['name']: value}})
        return Done(kept, note=str(len(items) - empty) + ' named ' + settings['name'] + (' · ' + str(empty) + ' had nothing to take' if empty else ''))


@register
class SetValue(Block):
    key, name, group, reach, takes, gives = 'set_value', 'Set a value', 'data', 'local', ('endpoint', 'domain'), 'same'
    says = 'Puts a fixed value on every result, or text built from other values such as https://{{hostname}}/login.'
    fields = (Field('name', 'Name', 'text', required=True), Field('text', 'Value', 'text', required=True, template=True))

    def clean(self, settings):
        cleaned = super().clean(settings)
        _name(cleaned['name'])
        return cleaned

    names_setting = 'name'

    def adds(self, settings):
        return [settings['name']]

    def begin(self, ctx, items, settings, allowance):
        kept, empty = [], 0
        for item in items:
            value = render(settings['text'], item)
            if value is None: empty += 1; continue
            kept.append({**item, 'fields': {**(item.get('fields') or {}), settings['name']: value}})
        return Done(kept, note=str(len(kept)) + ' set' + (' · ' + str(empty) + ' dropped (a value it uses was empty)' if empty else ''))
