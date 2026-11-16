"""Explicit per-execution minimum-use policy; never a promise of free requests."""


def validate_demo(value=False):
    if not isinstance(value,bool): raise ValueError('Demo must be true or false')
    return value


def demo_snapshot(snapshot,demo=False):
    result=dict(snapshot)
    enabled=validate_demo(demo) or result.get('collection_mode')=='demo'
    if enabled:
        result.update(collection_mode='demo',page_count=1,page_size=1,max_results=1,actions=[],auto_fetch=False)
    return result


def demo_metadata(**limits):
    return {'demo':True,'usage_policy':{'mode':'demo',**limits,
        'note':'Real minimal requests. Provider page sizes and billing units may be fixed; one saved result does not guarantee one billed credit.'}}
