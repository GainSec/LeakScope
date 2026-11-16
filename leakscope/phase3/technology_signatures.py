"""Typed response hints. A fingerprint is neither ownership nor binary attestation."""
import re

KINDS=('ja4','ja4s','ja4t','ja4ts','ouroboros')
NATIVE_SCOPE={'direction':'response','probe':'ouroboros.network-magic-mismatch.v1','protocol':0,'version':15,'message_type':2,'reason_code':2}
PATTERNS={
    'ja4':r'[tqd][0-9]{2}[di][0-9]{4}[a-zA-Z0-9]{2}_[0-9a-f]{12}_[0-9a-f]{12}',
    'ja4s':r'[tq][0-9]{4}[a-zA-Z0-9]{2}_[0-9a-f]{4}_[0-9a-f]{12}',
    'ja4t':r'[0-9]{1,5}_(?:[0-9]{1,3})(?:-[0-9]{1,3})*_[0-9]{2,5}_(?:00|[0-9]{1,2})',
    'ja4ts':r'[0-9]{1,5}_(?:[0-9]{1,3})(?:-[0-9]{1,3})*_[0-9]{2,5}_(?:00|[0-9]{1,2})(?:_[0-9]+(?:-[0-9]+)*(?:-R[0-9]+)?)?',
}

def validate_fingerprint(kind,value):
    if kind not in KINDS or not isinstance(value,str) or not 1<=len(value)<=1000:raise ValueError('Invalid fingerprint type or value')
    if kind=='ouroboros':
        if any(ord(c)<32 for c in value):raise ValueError('Invalid native response value')
    elif not re.fullmatch(PATTERNS[kind],value):raise ValueError('Invalid '+kind.upper()+' fingerprint')
    if kind in ('ja4t','ja4ts'):
        window,options,mss,scale,*_=value.split('_')
        if int(window)>65535 or int(mss)>65535 or int(scale)>14 or any(int(v)>255 for v in options.split('-')):raise ValueError('Invalid TCP fingerprint fields')
    return value

def validate_rule(data):
    allowed={'name','technology','kind','value','match','description','enabled'}
    if not isinstance(data,dict) or set(data)-allowed:raise ValueError('Unsupported signature field')
    for field,limit in [('name',160),('technology',100),('description',4000)]:
        value=data.get(field,'')
        if not isinstance(value,str) or len(value)>limit or any(ord(c)<32 and c not in '\n\t' for c in value):raise ValueError('Invalid '+field)
    name=data.get('name','').strip();technology=data.get('technology','').strip().lower()
    if not name or not re.fullmatch(r'[a-z0-9][a-z0-9_-]{0,99}',technology):raise ValueError('Enter a name and a technology key')
    kind=data.get('kind');value=validate_fingerprint(kind,data.get('value'));match=data.get('match','exact');enabled=data.get('enabled',True)
    if match not in ('exact','prefix') or match=='prefix' and kind!='ouroboros':raise ValueError('JA4 fingerprints require exact matching; only native refusals allow prefixes')
    if not isinstance(enabled,bool):raise ValueError('Enabled must be a boolean')
    return dict(name=name,technology=technology,kind=kind,value=value,match=match,enabled=enabled,description=data.get('description','').strip(),scope=dict(NATIVE_SCOPE) if kind=='ouroboros' else {})

def fingerprints(metadata):
    out=[];seen=set();items=metadata.get('fingerprints',[])
    items=list(items[:128]) if isinstance(items,list) else []
    for parent in ('ssl','tls','tcp'):
        node=metadata.get(parent)
        if isinstance(node,dict):
            for kind in KINDS[:-1]:
                if isinstance(node.get(kind),str):items.append({'kind':kind,'value':node[kind],'direction':'request' if kind in ('ja4','ja4t') else 'response','source':parent+'.'+kind})
    for item in items:
        if not isinstance(item,dict):continue
        try:validate_fingerprint(item.get('kind'),item.get('value'))
        except ValueError:continue
        if any(k in item and not isinstance(item[k],(str,int)) for k in ('direction','probe','protocol','version','message_type','reason_code','subject')):continue
        key=(item['kind'],item['value'],item.get('direction'),item.get('probe'))
        if key in seen:continue
        seen.add(key);out.append({k:item[k] for k in ('kind','value','direction','probe','protocol','version','message_type','reason_code','source','subject') if k in item})
    return out

def annotate(metadata):
    from leakscope_app.models import TechnologySignature
    result=dict(metadata)
    for key in ('signature_matches','signature_status','implementation','implementation_basis'):result.pop(key,None)
    observed=fingerprints(metadata);matches=[]
    if not observed and result==metadata:return result
    if observed:
        rules=list(TechnologySignature.objects.filter(enabled=True)[:1000])
        for rule in rules:
            for fp in observed:
                if rule.kind!=fp['kind']:continue
                if rule.kind in ('ja4','ja4t') and fp.get('subject')!='endpoint':continue
                if rule.kind=='ouroboros' and any(type(fp.get(k)) is not type(v) or fp.get(k)!=v for k,v in NATIVE_SCOPE.items()):continue
                if rule.kind!='ouroboros' and fp.get('direction')!=('request' if rule.kind in ('ja4','ja4t') else 'response'):continue
                if (fp['value']==rule.value if rule.match=='exact' else fp['value'].startswith(rule.value)):
                    matches.append({'id':str(rule.pk),'technology':rule.technology,'name':rule.name,'kind':rule.kind,'value':fp['value'],'rule_updated_at':rule.updated_at.isoformat(),'basis':'response signature; experimental' if rule.kind=='ouroboros' else 'fingerprint signature; hint only'});break
    technologies={m['technology'] for m in matches};result['signature_matches']=matches;result['signature_status']='ambiguous' if len(technologies)>1 else 'matched' if matches else 'unknown'
    if len(technologies)==1 and any(m['kind']=='ouroboros' for m in matches):
        result.update(implementation=next(iter(technologies)),implementation_basis='response signature; experimental')
    if observed:result['fingerprints']=observed
    return result
