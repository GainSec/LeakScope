"""Pure plans for provider-only collection and bounded endpoint captures."""
from .capture import _endpoint, supported_actions

COLLECTION_MODES = ('passive','demo','semi_passive','screenshot','full')


def validate_collection_mode(value, allow_custom=False):
    if isinstance(value,str) and (value in COLLECTION_MODES or (allow_custom and value=='')):
        return value
    raise ValueError('Select Passive, Demo, Semi-passive, Screenshot or Full collection')


def plan_collection(endpoint, mode, tools=None):
    mode=validate_collection_mode(mode)
    result={'mode':mode,'actions':[],'skipped':[]}
    if mode in {'passive','demo'}:return result
    if tools is not None and not isinstance(tools,dict):raise ValueError('Invalid capture tool availability')
    _,_,scheme,service,transport=_endpoint(endpoint)
    capabilities=set(supported_actions(endpoint))
    hint=endpoint.get('protocol_hint') if isinstance(endpoint.get('protocol_hint'),dict) else None
    if hint:
        from .protocol_hints import service_label
        result['routing']={'service':service,'basis':'provider_hint','provider':str(hint.get('provider') or '')[:32],'evidence':str(hint.get('evidence') or '')[:200],
            'note':'Reader chosen from provider-reported '+service_label(service)+' response text. This is not a verification unless the live exchange succeeds.'}
    available=lambda name: tools is None or tools.get(name) is True
    if service=='vnc' and transport=='tcp':
        result['actions']=['screenshot'] if mode in ('screenshot','full') else ['check']
        return result
    # Conventional web ports are manual compatibility hints. Automatic collection
    # requires an observed HTTP scheme or a profile known to expose HTTP.
    http='http' in capabilities and (scheme in ('http','https') or service not in ('','unknown','unclassified'))
    skipped=result['skipped']
    if mode=='screenshot':
        if not http:skipped.append('Screenshot skipped: no eligible HTTP interface is identified for this endpoint.')
        elif not (available('playwright') or available('gowitness')):skipped.append('Screenshot skipped: the browser capture tools are unavailable.')
        else:result['actions']=['screenshot']
        return result
    if http:
        if mode=='semi_passive':
            if available('httpx'):result['actions']=['httpx'];return result
            skipped.append('HTTP fingerprint (httpx) is unavailable; using an available fallback.')
            if available('curl'):result['actions']=['http'];return result
            skipped.append('HTTP response (curl) is unavailable; only TCP reachability can be checked.')
        else:
            for action,tool,label in [('http','curl','HTTP response (curl)'),('httpx','httpx','HTTP fingerprint (httpx)'),('screenshot','gowitness','Screenshot (browser)')]:
                if available(tool) or (action=='screenshot' and available('playwright')):result['actions'].append(action)
                else:skipped.append(label+' skipped: the capture tool is unavailable.')
            if result['actions']:return result
            skipped.append('No HTTP capture tools are available; only TCP reachability can be checked.')
        result['actions']=['check']
        return result
    if 'identity' in capabilities:
        result['actions']=['identity']
        if mode=='full':skipped.append('HTTP captures and screenshots are skipped for this native endpoint; native identity already retains its wire evidence.')
        return result
    from .native import identity_capability
    limitation=identity_capability(service)['limitation']
    if limitation:skipped.append(limitation)
    elif service not in ('','unknown','unclassified'):
        from .protocol_hints import service_label
        skipped.append('Unsupported: LeakScope has no native '+service_label(service)+' reader, so this protocol cannot be checked live.')
    if transport=='tcp':
        result['actions']=['check','tcp'] if mode=='full' else ['check']
        skipped.append('No implemented native or HTTP fingerprint applies; TCP connection reachability does not verify protocol identity.')
    else:
        skipped.append('UDP reachability cannot be established by connecting a socket. No supported automatic identity handshake applies; an explicit manual payload is required.')
    return result
