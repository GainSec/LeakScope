"""Port-independent, co-occurring web UI candidates from supplied examples."""
DEFINITIONS = {
    'wago_hmi': ('WAGO controller web interface', ('WAGO','Hostname:'), 'https://techdocs.wago.com/Software/Device_Sphere/en-US/4486684939.html'),
    'codesys_webvisu': ('CODESYS WebVisu', ('CODESYS','webvisu'), 'https://us.store.codesys.com/codesys-web-view.html'),
    'keba_kecontact': ('KEBA KeContact web interface', ('KEBA','KeContact'), 'https://www.keba.com/en/emobility/digital-solutions/emobility-portal/'),
    'comet_websensor': ('COMET Web Sensor', ('Web Sensor','MinMax'), 'https://www.cometsystem.com/userfiles/dokumenty_menu/3/comet_catalogue_co2_eng.pdf'),
    'kaco_blueplanet': ('KACO blueplanet web monitor', ('blueplanet','kVA'), 'https://kaco-newenergy.com/products/'),
    'rockware_magicimm': ('Rockware Magic IMM web panel', ('Magic IMM','Manufacturing Execution System'), 'Supplied example screenshot'),
    'ospa_pool': ('OSPA pool web interface', ('OSPA','PISCINA'), 'https://www.ospa-schwimmbadtechnik.de/en/water-treatment/measurement-control-technology/ospa-bluecontrol-5-web'),
    'pump_status_hmi': ('Status PW water pump HMI', ('Status PW','Wasserh'), 'Supplied example screenshot'),
}
HMI_TYPES=frozenset(DEFINITIONS)
PROFILES={key:{'summary':label+' indexed web UI candidate from co-occurring response strings on any indexed port. '
    'Screenshot-derived visual text may be absent from indexed HTML or vary by version/language; current identity and access are unverified.',
    'signals':tuple(('body',term) for term in terms),'sources':(source,)} for key,(label,terms,source) in DEFINITIONS.items()}


def match_hmi(title='',body=''):
    text=(str(title)[:2000]+'\n'+str(body)[:262144]).casefold()
    return [{'key':key,'label':label,'confidence':'candidate','matched_terms':list(terms),'source':source}
        for key,(label,terms,source) in DEFINITIONS.items() if all(term.casefold() in text for term in terms)]
