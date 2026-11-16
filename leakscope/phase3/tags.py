"""Tag presentation keeps manual ownership distinct from inherited assignments."""
from leakscope_app.models import TagAssignment


def tag_details(kind, key):
    rows = list(TagAssignment.objects.filter(target_kind=kind, target_key=str(key)).values_list('tag__name', 'origin'))
    return {'tags': sorted({tag for tag, _ in rows}),
            'manual_tags': sorted({tag for tag, origin in rows if origin == 'manual'}),
            'inherited_tags': sorted({tag for tag, origin in rows if origin != 'manual'})}
