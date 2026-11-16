from django.db import migrations
import uuid

IDS=[uuid.UUID('bd35da7c-48c7-4dca-8908-a390137caa01'),uuid.UUID('bd35da7c-48c7-4dca-8908-a390137caa02')]
def seed(apps,schema_editor):
    Model=apps.get_model('leakscope_app','TechnologySignature')
    scope={'direction':'response','probe':'ouroboros.network-magic-mismatch.v1','protocol':0,'version':15,'message_type':2,'reason_code':2}
    for pk,technology,name,value,match in [
        (IDS[0],'amaru','Amaru · native refusal','network magic mismatch','exact'),
        (IDS[1],'cardanonode','Haskell cardano-node · native refusal','version data mismatch: NodeToNodeVersionData {','prefix')]:
        Model.objects.using(schema_editor.connection.alias).get_or_create(pk=pk,defaults=dict(name=name,technology=technology,kind='ouroboros',value=value,match=match,scope=scope,description='Experimental response-family signature. Tested on three releases per client and two Linux kernels in the 2026-11-12 brabox lab. Can be imitated; unanswered probes stay unknown. MUX framing or JA4TS alone does not identify implementation.'))
def unseed(apps,schema_editor):
    apps.get_model('leakscope_app','TechnologySignature').objects.using(schema_editor.connection.alias).filter(pk__in=IDS).delete()
class Migration(migrations.Migration):
    dependencies=[('leakscope_app','0037_technology_signatures')]
    operations=[migrations.RunPython(seed,unseed)]
