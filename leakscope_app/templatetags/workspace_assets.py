from django import template
from django.conf import settings

from leakscope_app.ui_bundles import BUNDLES

register = template.Library()


@register.simple_tag
def workspace_assets(kind):
    target = {'css': 'css/workspace_bundle.css', 'js': 'js/leakscope/workspace_bundle.js'}[kind]
    return BUNDLES[target] if settings.DEBUG else (target,)
