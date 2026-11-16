"""Cached native IP context and explicit bounded official network-feed refresh."""
from django.http import JsonResponse

from leakscope.phase3 import cloud_ranges
from leakscope.phase3.asset_names import normalize_ip
from .workbench import endpoint, _body


@endpoint(('GET', 'POST'))
def context(request):
    if request.method == 'GET':
        if request.GET.keys() - {'ip'}:
            raise ValueError('Network context accepts a concrete IP only')
        ip = normalize_ip(request.GET.get('ip'))
        return JsonResponse({'ip': ip, 'matches': cloud_ranges.lookup([ip]),
            'datasets': cloud_ranges.dataset_status(), 'basis': cloud_ranges.BASIS, 'network_contact': False})
    data = _body(request)
    if not isinstance(data, dict) or data.keys() != {'providers'}:
        raise ValueError('Choose the official network feeds to refresh')
    return JsonResponse(cloud_ranges.refresh(data['providers']))
