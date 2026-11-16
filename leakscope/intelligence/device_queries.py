"""Typed native-device evidence and deterministic provider query compilation.

Shodan and ZoomEye match text inside a classified service banner; they do not
claim an exact vendor-field match. Netlas uses its published decoded field paths.
No provider or device requests occur here.
"""
import re

SHODAN_SYNTAX='https://book.shodan.io/getting-started/query-syntax/'
SHODAN_MODULES='https://developer.shodan.io/api#shodan-protocols'
ZOOMEYE_SYNTAX='https://github.com/zoomeye-ai/ZoomEye-python/blob/master/api.md'
NETLAS_FIELDS='https://docs.netlas.io/knowledge-base/field-reference/responses/'
NETLAS_SYNTAX='https://docs.netlas.io/knowledge-base/query-language/'
FIELD_MAP={
    'modbus.vendor':('modbus','modbus.mei_response.objects.vendor'),
    'modbus.product':('modbus','modbus.mei_response.objects.product_code'),
    'modbus.revision':('modbus','modbus.mei_response.objects.revision'),
    'bacnet.vendor':('bacnet','bacnet.vendor_name'),
    'bacnet.model':('bacnet','bacnet.model_name'),
    'bacnet.firmware':('bacnet','bacnet.firmware_revision'),
    'bacnet.application':('bacnet','bacnet.application_software_revision'),
}
NATIVE_FIELDS=frozenset(FIELD_MAP)
PROVIDER_SOURCES={
    'shodan':[SHODAN_SYNTAX,SHODAN_MODULES],
    'zoomeye':[ZOOMEYE_SYNTAX],
    'netlas':[NETLAS_SYNTAX,NETLAS_FIELDS],
}


def compile_device_queries(groups,mode,scope):
    from .queries import compile_queries,_quote,_url,_scope_constraints
    result={}
    regular={field:compile_queries([{'field':field,'value':value} for value in values])
             for field,values in groups.items() if field not in NATIVE_FIELDS}
    for provider in ('shodan','zoomeye','netlas'):
        warnings=[];parts=[]
        if provider=='shodan' and mode=='any' and len(groups)>1:
            warnings.append('Shodan has no verified cross-field OR mapping; select ALL or one native value.')
        for field,values in groups.items():
            if field not in NATIVE_FIELDS:
                dto=regular[field][provider]
                if not dto['supported']:warnings.extend(dto['warnings']);continue
                parts.append(dto['query'] if provider=='shodan' else '('+dto['query']+')');continue
            protocol,netlas_field=FIELD_MAP[field]
            if provider=='shodan':
                if len(values)>1:
                    warnings.append(field+' has alternative banner values; no verified Shodan banner-OR mapping exists.')
                    continue
                parts.append('shodan.module:'+protocol+' '+_quote(values[0],provider))
            elif provider=='zoomeye':
                service='(service="modbus" || service="modbus-tcp")' if protocol=='modbus' else '(service="bacnet" || service="bacnet-ip")'
                terms=['banner='+_quote(value,provider) for value in values]
                parts.append('('+service+' && ('+' || '.join(terms)+'))')
            else:
                terms=[netlas_field+':'+_quote(value,provider) for value in values]
                parts.append('('+' OR '.join(terms)+')' if len(terms)>1 else terms[0])
        if warnings:
            result[provider]={'query':'','url':'','supported':False,'warnings':warnings};continue
        join=' ' if provider=='shodan' else (' && ' if provider=='zoomeye' else ' AND ')
        any_join=' || ' if provider=='zoomeye' else ' OR '
        query=(join if mode=='all' or provider=='shodan' else any_join).join(parts)
        constraints=_scope_constraints(provider,scope)
        if constraints:query=(query if provider=='shodan' else '('+query+')')+join+join.join(constraints)
        notes=['Native identity candidates from classified banner text; indexed text and classifier coverage vary.'] if provider!='netlas' else ['Native identity candidates from documented decoded response fields; index coverage varies.']
        result[provider]={'query':query,'url':_url(provider,query),'supported':True,'warnings':notes}
    return result


def observed_device_values(metadata,service_type=''):
    """Return only named values actually present in saved evidence, never labels
    guessed from register data, address/port, product taxonomy or a site title.
    """
    if not isinstance(metadata,dict):return []
    roots=[metadata]
    for parent in tuple(roots):
        for key in ('raw','data'):
            child=parent.get(key)
            if isinstance(child,dict):roots.append(child)
    for parent in tuple(roots[1:]):
        child=parent.get('data')
        if isinstance(child,dict):roots.append(child)
    values=[]
    def add(field,value):
        if isinstance(value,(str,int)) and not isinstance(value,bool) and str(value).strip():values.append((field,value))
    for root in roots[:6]:
        protocol=str(root.get('service') or root.get('prot7') or root.get('protocol') or service_type).lower()
        if protocol in {'tcp','udp'}:protocol=str(service_type).lower()
        device=root.get('device')
        if not isinstance(device,dict):device={}
        modbus=root.get('modbus')
        if not isinstance(modbus,dict):modbus={}
        bacnet=root.get('bacnet')
        if not isinstance(bacnet,dict):bacnet={}
        if protocol.startswith('modbus') or modbus:
            objects=device.get('objects',root.get('objects'))
            if isinstance(objects,list):
                for item in objects[:32]:
                    if not isinstance(item,dict) or not isinstance(item.get('id'),(str,int)) or isinstance(item.get('id'),bool):continue
                    field={0:'modbus.vendor',1:'modbus.product',2:'modbus.revision','0':'modbus.vendor','1':'modbus.product','2':'modbus.revision'}.get(item.get('id'))
                    if field:add(field,item.get('value'))
            mei=modbus.get('mei_response')
            named=mei.get('objects') if isinstance(mei,dict) else None
            if isinstance(named,dict):
                for key,field in [('vendor','modbus.vendor'),('product_code','modbus.product'),('revision','modbus.revision')]:add(field,named.get(key))
            for key,field in [('VendorName','modbus.vendor'),('ProductCode','modbus.product'),('Revision','modbus.revision')]:add(field,device.get(key))
            for banner in [root.get('data'),root.get('banner')]:
                if not isinstance(banner,str):continue
                for key,field in [('VendorName','modbus.vendor'),('ProductCode','modbus.product'),('Revision','modbus.revision')]:
                    match=re.search(r'(?:^|[\r\n|])\s*'+key+r'\s*:\s*([^\r\n|]{1,1000})',banner[:65536],re.I)
                    if match:add(field,match[1].strip())
        if protocol.startswith('bacnet') or bacnet:
            for field,keys in [
                ('bacnet.vendor',('vendor_name','Vendor Name')),
                ('bacnet.model',('model_name','Model Name')),
                ('bacnet.firmware',('firmware_revision','Software Version')),
                ('bacnet.application',('application_software_version','application_software_revision','Application Version')),
            ]:
                for obj in (device,bacnet):
                    for key in keys:add(field,obj.get(key))
    return values
