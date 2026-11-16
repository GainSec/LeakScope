from django.http import JsonResponse
from .models import TechnologySignature
from .workbench import endpoint,_body
from leakscope.phase3.technology_signatures import KINDS,validate_rule

def serialize(rule):
    return {**{k:getattr(rule,k) for k in ('name','technology','kind','value','match','scope','description','enabled')},'id':str(rule.pk),'updated_at':rule.updated_at.isoformat()}

@endpoint(('GET','POST'))
def signatures(request,signature_id=None,action=None):
    if request.method=='GET':
        if signature_id:raise ValueError('Use the signature list')
        return JsonResponse({'results':[serialize(r) for r in TechnologySignature.objects.all()[:1000]],'kinds':list(KINDS),'limit':1000})
    if action=='delete':
        rule=TechnologySignature.objects.get(pk=signature_id);rule.delete();return JsonResponse({'deleted':True})
    data=_body(request)
    if signature_id:
        rule=TechnologySignature.objects.get(pk=signature_id);current={k:getattr(rule,k) for k in ('name','technology','kind','value','match','description','enabled')};current.update(data);data=current
    else:
        if TechnologySignature.objects.count()>=1000:raise ValueError('Signature limit reached')
        rule=TechnologySignature()
    values=validate_rule(data)
    for k,v in values.items():setattr(rule,k,v)
    rule.save();return JsonResponse({'signature':serialize(rule)},status=200 if signature_id else 201)

@endpoint(('POST',))
def import_capture(request):
    import hashlib,json,uuid
    from django.db import transaction
    from django.utils import timezone
    from leakscope.phase3.ja4_capture import analyze_pcap,MAX_CAPTURE
    from leakscope.phase3.state import ingest_observation
    from leakscope.phase3.jobs import capture_root
    from .models import EvidenceArtifact
    upload=request.FILES.get('capture')
    if upload is None or upload.size>MAX_CAPTURE:raise ValueError('Upload a classic PCAP up to 4 MiB')
    data=upload.read(MAX_CAPTURE+1);rows=analyze_pcap(data);digest=hashlib.sha256(data).hexdigest();saved=[]
    save=request.POST.get('save','0')
    if save not in ('0','1'):raise ValueError('Invalid save choice')
    if save=='1':
        if not rows:raise ValueError('No supported TCP/TLS fingerprints were found')
        root=capture_root();folder=root/'fingerprints';folder.mkdir(parents=True,exist_ok=True,mode=0o700);path=folder/(str(uuid.uuid4())+'.pcap')
        path.write_bytes(data);path.chmod(0o600)
        try:
            with transaction.atomic():
                endpoints={}
                for row in rows:
                    endpoint=ingest_observation('import:ja4',{'address':row['address'],'port':row['port'],'transport':'tcp','source_id':'pcap:'+digest,'observed_at':None,'metadata':{'fingerprints':row['fingerprints'],'capture_sha256':digest,'observation_note':'Imported PCAP; packet timestamps are not proof of current reachability'}})
                    endpoints[endpoint.pk]=endpoint
                for endpoint in endpoints.values():
                    artifact=EvidenceArtifact.objects.create(endpoint=endpoint,kind='packet-capture',relative_path=str(path.relative_to(root)),mime='application/vnd.tcpdump.pcap',size=len(data),sha256=digest,tool='ja4',metadata={'format':'classic-pcap','source':'user import','subject_note':'JA4/JA4T belong to the initiator; JA4S/JA4TS belong to the responder'})
                    saved.append({'endpoint_id':str(endpoint.pk),'artifact_id':str(artifact.pk)})
        except BaseException:
            path.unlink(missing_ok=True);raise
    return JsonResponse({'results':rows,'sha256':digest,'saved':saved,'count':len(rows),'scope':'Offline analysis only; no target connections','limitations':['Classic PCAP, TCP, IPv4 and IPv6 without extension headers only','No QUIC/DTLS, TLS decryption or JA4TScan retransmission analysis','Missing or fragmented handshake bytes are not guessed']})
