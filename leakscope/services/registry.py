"""Supported service types and aliases, independent of transport and UI."""
from leakscope_app import models

_MODELS={
 'angular':'Angular','amazons3be':'Amazons3be','amazonbe':'Amazonbe','amazonbuckets':'AmazonBuckets',
 'gitlab':'Gitlab','elastic':'Elastic','dirs':'Dirs','jenkins':'Jenkins','mongo':'Mongo','rsync':'Rsync',
 'sonarqube':'Sonarqube','couchdb':'Couchdb','kibana':'Kibana','cassandra':'Cassandra','rethink':'Rethink',
 'ftp':'Ftp','opensearch':'OpenSearch','grafana':'Grafana','prometheus':'Prometheus','minio':'Minio',
 'swagger':'Swagger','mongoexpress':'MongoExpress','keys':'Keys','javascript':'Javascript','github':'Github',
 'nexus':'Nexus','artifactory':'Artifactory','registry':'Registry','etcd':'Etcd','consul':'ConsulKV',
 'rabbitmq':'Rabbitmq','solr':'Solr','gitea':'Gitea','gogs':'Gogs','azureblob':'AzureBlob','gcsbucket':'GcsBucket'}
ALIASES={'api_key':'keys','stripe':'keys','secret_key':'keys','google_api_key':'keys','asia':'amazonbe','europe':'amazonbe','north america':'amazonbe','harbor':'registry','dockerapi':'registry','consulkv':'consul','elasticsearch':'elastic','rethinkdb':'rethink'}
_LABELS={'elastic':'Elasticsearch','opensearch':'OpenSearch','amazons3be':'S3 listing','amazonbe':'S3 references','amazonbuckets':'S3 buckets','consul':'Consul KV','gcsbucket':'Google Cloud Storage','azureblob':'Azure Blob','mongoexpress':'Mongo Express','keys':'Key indicators','javascript':'JavaScript','github':'Git repository','dirs':'Directory listing','rsync':'Rsync','ftp':'FTP','rabbitmq':'RabbitMQ','nexus':'Nexus','artifactory':'Artifactory','registry':'Container registry'}


def canonical_type(name):
    key=str(name or '').strip().lower()
    return ALIASES.get(key,key)


def model_for_type(name):
    value=_MODELS.get(canonical_type(name))
    return getattr(models,value) if value else None


# handler name, accepts keyword, accepts active_probe, optional product hint
_DISCOVERY={
 'gitlab':('check_gitlab',False,False,None),
 'elastic':('check_elastic',True,True,None),
 'opensearch':('check_opensearch',True,True,None),
 'angular':('check_angular',True,False,None),
 'amazons3be':('check_amazons3be',True,False,None),
 'dirs':('check_dir',True,False,None),
 'jenkins':('check_jenkins',True,False,None),
 'mongo':('check_mongo',True,False,None),
 'rsync':('check_rsync',False,False,None),
 'ftp':('check_ftp',True,False,None),
 'sonarqube':('check_sonarqube',False,False,None),
 'couchdb':('check_couchdb',False,False,None),
 'kibana':('check_kibana',False,False,None),
 'cassandra':('check_cassandra',False,False,None),
 'rethink':('check_rethink',False,False,None),
 'grafana':('check_grafana',False,False,None),
 'prometheus':('check_prometheus',False,False,None),
 'minio':('check_minio',False,False,None),
 'swagger':('check_swagger',False,False,None),
 'mongoexpress':('check_mongoexpress',False,False,None),
 'nexus':('check_nexus',False,False,None),
 'artifactory':('check_artifactory',False,False,None),
 'registry':('check_registry',False,False,None),
 'etcd':('check_etcd',False,True,None),
 'consul':('check_consul',False,True,None),
 'rabbitmq':('check_rabbitmq',False,True,None),
 'solr':('check_solr',False,True,None),
 'gitea':('check_gitea',False,True,'gitea'),
 'gogs':('check_gitea',False,True,'gogs'),
 'azureblob':('check_azureblob',False,True,None),
 'gcsbucket':('check_gcsbucket',False,True,None),
 'keys':('check_keys',False,False,None),
 'amazonbe':('check_amazonbe',False,False,None),
}
# Preview protocol is explicit. Metadata-only previews do not claim to have
# queried a database merely because a web administration UI was discovered.
_PREVIEW={
 'elastic':('_preview_elastic','ip_port'),'opensearch':('_preview_elastic','ip_port'),
 'dirs':('_preview_dirs','url'),'jenkins':('_preview_jenkins','url'),
 'ftp':('_preview_ftp','ip_port'),'couchdb':('_preview_couch','ip_port'),
 'mongo':('_preview_mongo','ip_port'),'mongoexpress':(None,'metadata'),
 'rsync':('_preview_rsync','ip_port'),'nexus':('_preview_nexus','url'),
 'artifactory':('_preview_artifactory','url'),'registry':('_preview_registry','url'),
 'amazons3be':('_preview_s3','bucket'),'amazonbuckets':('_preview_s3','bucket'),
 'gitlab':('_preview_gitlab','url'),'sonarqube':('_preview_sonarqube','url'),
 'kibana':('_preview_kibana','url'),'grafana':('_preview_grafana','url'),
 'prometheus':('_preview_prometheus','url'),'swagger':('_preview_swagger','url'),
 'minio':('_preview_minio','url'),'angular':('_preview_angular','url'),
 'cassandra':('_preview_cassandra','ip_port'),'rethink':('_preview_rethink','ip_port'),
 'keys':('_preview_keys','object'),'amazonbe':('_preview_amazonbe','object'),
 'javascript':('_preview_javascript','object'),'github':('_preview_github','object'),
 'etcd':('_preview_etcd','ip_port'),'consul':('_preview_consul','ip_port'),
 'rabbitmq':('_preview_rabbitmq','ip_port'),'solr':('_preview_solr','ip_port'),
 'gitea':('_preview_gitea','ip_port'),'gogs':('_preview_gitea','ip_port'),
 'azureblob':('_preview_azureblob','ip_port'),'gcsbucket':('_preview_gcsbucket','ip_port'),
}
_LIVE_EXPORT={'elastic','opensearch','mongo','couchdb','amazons3be','amazonbuckets','nexus','artifactory','registry','solr','azureblob','gcsbucket','etcd','consul','rabbitmq'}


def discovery_handler(type_name):
    from leakscope import tasks
    spec=_DISCOVERY.get(canonical_type(type_name))
    return getattr(tasks,spec[0]) if spec else None


def dispatch_discovery(type_name,index,match,search,config,keyword=None,active_probe=False):
    spec=_DISCOVERY.get(canonical_type(type_name))
    if spec is None:raise ValueError('Discovery not supported for this service type')
    kwargs={'config':config}
    if spec[1]:kwargs['keyword']=keyword
    if spec[2]:kwargs['active_probe']=active_probe
    if spec[3]:kwargs['product_hint']=spec[3]
    return discovery_handler(type_name)(index,match,search,**kwargs)


def preview_for_type(type_name,obj):
    from leakscope import tasks
    spec=_PREVIEW.get(canonical_type(type_name))
    if spec is None:raise ValueError('Preview not supported for this service type')
    name,shape=spec
    if shape=='metadata':
        return {'summary':'Stored service metadata; no native database protocol assumed',
                'items':[{'ip':getattr(obj,'ip',''),'port':getattr(obj,'port',''),'url':getattr(obj,'url',''),'title':getattr(obj,'title','')}]}
    handler=getattr(tasks,name)
    if shape=='ip_port':return handler(obj.ip,obj.port)
    if shape=='object':return handler(obj)
    if shape=='url':return handler(getattr(obj,'url',None) or tasks._http_base(obj.ip,obj.port))
    bucket=getattr(obj,'bucket',None)
    if not bucket and hasattr(obj,'buckets'):
        from .identity import parse_list
        values=parse_list(obj.buckets);bucket=values[0] if values else None
    if not bucket:return {'summary':'No bucket found','items':[]}
    return handler(str(bucket).replace('https://','').replace('http://',''))


def service_types():
    return [{'key':key,'label':_LABELS.get(key,key.title()),'model':getattr(models,name),
             'aliases':[alias for alias,canonical in ALIASES.items() if canonical==key],
             'discovery':key in _DISCOVERY,'query_type':{'keys':'api_key','amazonbe':'asia'}.get(key,key),
             'preview':key in _PREVIEW,'preview_mode':'metadata' if _PREVIEW.get(key,(None,'metadata'))[1] in ('metadata','object') else 'live',
             'export':key in _MODELS,'export_mode':'live' if key in _LIVE_EXPORT else 'metadata',
             'explore':key not in ('keys','grafana','prometheus','swagger','kibana','mongoexpress')}
            for key,name in _MODELS.items()]
