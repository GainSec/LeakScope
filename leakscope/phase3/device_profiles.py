"""Built-in vendor/model discovery profiles, independent of listening ports."""
from leakscope.intelligence.device_queries import PROVIDER_SOURCES
from leakscope.intelligence.queries import compile_queries


def _profile(label,service_type,*signals):
    signals=[{'field':field,'value':value} for field,value in signals]
    summary=(label+' candidate from reported native device-identification strings on any indexed port. '
             'Shodan/ZoomEye match co-occurring classified banner text; Netlas matches decoded fields. '
             'Coverage and formatting vary; matching does not authenticate the reported vendor/model or current access.')
    queries={provider:{'query':dto['query'],'supported':dto['supported'],'reason':summary if dto['supported'] else ' '.join(dto['warnings']),
                       'basis':list(PROVIDER_SOURCES[provider])}
             for provider,dto in compile_queries(signals).items()}
    sources=list(dict.fromkeys(source for values in PROVIDER_SOURCES.values() for source in values))
    return {'label':label,'service_type':service_type,'signals':signals,'queries':queries,'summary':summary,'sources':sources}


DEVICE_PROFILES={
    'delta_modbus':_profile('Delta Electronics / Modbus','modbus',('modbus.vendor','Delta Electronics')),
    'delta_as228t_a':_profile('Delta AS228T-A / Modbus','modbus',('modbus.vendor','Delta Electronics'),('modbus.product','AS228T-A')),
    'tridium_bacnet':_profile('Tridium / BACnet','bacnet',('bacnet.vendor','Tridium')),
    'niagara4_bacnet':_profile('Tridium Niagara4 Station / BACnet','bacnet',('bacnet.vendor','Tridium'),('bacnet.model','Niagara4 Station')),
}
