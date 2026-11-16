"""Existing HTTP workflows expressed as all-port response candidates.

These selectors use the response fields already exercised by the legacy code,
not provider product taxonomy. Titles and content indicators can be customized
or occur in documentation; they are candidates rather than identity verdicts.
"""
FIELD_SOURCES = (
    'https://book.shodan.io/getting-started/query-syntax/',
    'https://github.com/zoomeye-ai/ZoomEye-python/blob/master/api.md',
    'https://docs.netlas.io/knowledge-base/field-reference/responses/',
)

def _http(summary, *signals):
    return {'summary': summary + ' HTTP response candidate on any indexed port; confirm from captured evidence.',
            'signals': tuple(signals), 'sources': FIELD_SOURCES, 'overrides': ()}

PROFILES = {
    'angular': _http('JavaScript application shell with polyfills/main/runtime assets; this build layout alone does not verify Angular',
                     ('body','polyfills'),('body','main.'),('body','runtime')),
    'amazons3be': _http('S3-compatible ListBucketResult XML; it does not distinguish Amazon from compatible storage or establish write access',('body','ListBucketResult')),
    'amazonbe': _http('A page referring to s3.amazonaws.com; references do not establish an accessible bucket',('body','s3.amazonaws.com')),
    'gitlab': _http('GitLab web-interface title',('title','GitLab')),
    'dirs': _http('Directory listing title; body/content and permissions still need inspection',('title','Index of /')),
    'jenkins': _http('Jenkins X-Jenkins response-header presence',('header','X-Jenkins')),
    'sonarqube': _http('SonarQube web-interface title',('title','SonarQube')),
    'kibana': _http('Kibana kbn-name response-header presence',('header','kbn-name')),
    'grafana': _http('Grafana web-interface title',('title','Grafana')),
    'prometheus': _http('Prometheus web-interface title',('title','Prometheus')),
    'swagger': _http('Swagger UI initialization marker in response content; it identifies documentation UI, not API access permission',('body','SwaggerUIBundle')),
    'mongoexpress': _http('Mongo Express web-interface title',('title','Mongo Express')),
    'keys': _http('Literal api_key indicator in a page; examples and documentation may match and no credential validity is established',('body','api_key')),
    'nexus': _http('Nexus Repository Manager web-interface title',('title','Nexus Repository Manager')),
    'artifactory': _http('Artifactory web-interface title',('title','Artifactory')),
    'registry': _http('Docker distribution API-version response header; registry contents/authentication are not verified',('header','Docker-Distribution-Api-Version')),
    'rabbitmq': _http('RabbitMQ Management web-interface title; this is the HTTP companion rather than an AMQP listener',('title','RabbitMQ Management')),
    'solr': _http('Solr Admin web-interface title',('title','Solr Admin')),
    'gitea': _http('Gitea web-interface title',('title','Gitea')),
    'gogs': _http('Gogs web-interface title',('title','Gogs')),
    'azureblob': _http('Blob enumeration XML with EnumerationResults and Blobs; permissions/provider identity still need confirmation',('body','EnumerationResults'),('body','Blobs')),
    'gcsbucket': _http('Google-storage-style object generation response header; this does not imply bucket listing permission',('header','X-Goog-Generation')),
}
