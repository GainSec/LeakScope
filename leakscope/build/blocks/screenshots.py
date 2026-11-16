"""Saved screenshots: keep results by whether they have one, and file screenshots in a collection."""
from leakscope.build.kinds import ENDPOINT_FIELDS, refresh, gone
from leakscope.phase3.capture_status import IMAGE_KINDS
from leakscope.phase3.local_correlations import visible_artifacts
from leakscope.phase3.state import visible_endpoints
from .base import Block, BlockError, Field, Done, register


def _shots(items):
    """Visible saved screenshots for these results, newest first, as (artifact id, endpoint id)."""
    ids = [item['id'] for item in items]
    rows = visible_artifacts(visible_endpoints().filter(pk__in=ids)).filter(kind__in=IMAGE_KINDS).order_by('-created_at', '-pk')
    return [(pk, str(endpoint)) for pk, endpoint in rows.values_list('pk', 'endpoint_id')]


@register
class HasScreenshot(Block):
    key, name, group, reach, takes, gives = 'has_screenshot', 'Has a screenshot', 'logic', 'local', 'endpoint', 'endpoint'
    says = 'Keeps the results that have a saved screenshot, or the ones that do not.'
    output_fields = ENDPOINT_FIELDS
    fields = (Field('keep', 'Keep results', 'choice', default='with', choices=(('with', 'With a screenshot'), ('without', 'Without a screenshot'))),)

    def begin(self, ctx, items, settings, allowance):
        items, dropped = refresh(items)
        have = {endpoint for _, endpoint in _shots(items)}
        kept = [item for item in items if (item['id'] in have) == (settings['keep'] == 'with')]
        return Done(kept, note=str(len(kept)) + ' of ' + str(len(items)) + ' kept' + gone(dropped))


@register
class AddToCollection(Block):
    key, name, group, reach, takes, gives = 'add_to_collection', 'Add screenshots to a collection', 'organize', 'local', 'endpoint', 'endpoint'
    says = 'Files each result’s saved screenshots in a Screenshots collection, creating the collection if needed.'
    output_fields = ENDPOINT_FIELDS
    fields = (Field('collection', 'Collection', 'text', required=True, suggest='collections'),
              Field('which', 'Screenshots', 'choice', default='latest', choices=(('latest', 'The newest for each result'), ('all', 'Every saved screenshot'))),
              Field('keep', 'Also mark them as kept', 'flag', default=False))

    def begin(self, ctx, items, settings, allowance):
        from django.db import transaction
        from leakscope_app.models import ScreenshotCollection, ScreenshotCuration, ScreenshotMembership
        items, dropped = refresh(items)
        name = settings['collection'][:120]
        chosen, seen = [], set()
        for artifact, endpoint in _shots(items):
            if settings['which'] == 'latest' and endpoint in seen: continue
            seen.add(endpoint); chosen.append(artifact)
        added = 0
        with transaction.atomic():
            row = ScreenshotCollection.objects.filter(name=name).first()
            if row is None:
                if ScreenshotCollection.objects.count() >= 300: raise BlockError('At most 300 collections are supported')
                row = ScreenshotCollection.objects.create(name=name)
            for artifact in chosen:
                added += ScreenshotMembership.objects.get_or_create(artifact_id=artifact, collection=row)[1]
                if settings['keep']: ScreenshotCuration.objects.update_or_create(artifact_id=artifact, defaults={'pinned': True})
        bare = len(items) - len(seen)
        note = str(added) + ' screenshot' + ('' if added == 1 else 's') + ' added to “' + name + '”'
        if len(chosen) - added: note += ' · ' + str(len(chosen) - added) + ' already there'
        if bare: note += ' · ' + str(bare) + ' result' + (' has' if bare == 1 else 's have') + ' no screenshot'
        return Done(items, note=note + gone(dropped))
