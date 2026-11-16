"""Settings page: download the whole database, or replace it with a downloaded file."""
import tempfile

from django.http import FileResponse, JsonResponse
from django.utils import timezone

from leakscope import database_archive as archive
from .workbench import endpoint

CONFIRM = 'REPLACE'


def _overview():
    return {'import': archive.status(), 'importing': archive.importing(), 'busy_jobs': archive.busy_jobs(), 'confirm': CONFIRM}


@endpoint(("GET",))
def overview(request):
    return JsonResponse(_overview())


@endpoint(("GET",))
def download(request):
    if archive.importing():
        return JsonResponse({'error': 'An import is running. Export again when it has finished.'}, status=409)
    scratch = tempfile.TemporaryFile()
    archive.write(scratch)
    size = scratch.tell()
    scratch.seek(0)
    response = FileResponse(scratch, as_attachment=True, content_type='application/gzip',
                            filename='leakscope-database-%s.jsonl.gz' % timezone.now().strftime('%Y%m%d-%H%M%S'))
    response['Content-Length'] = str(size)
    response['Cache-Control'] = 'no-store'
    # Already compressed: keep the response middleware from compressing it again.
    response['Content-Encoding'] = 'identity'
    return response


@endpoint(("POST",))
def start_import(request):
    from leakscope import tasks
    upload = request.FILES.get('file')
    if upload is None:
        return JsonResponse({'error': 'Choose a LeakScope database file to import.'}, status=400)
    if request.POST.get('confirm') != CONFIRM:
        return JsonResponse({'error': 'Type %s to confirm. Importing replaces everything saved here.' % CONFIRM}, status=400)
    if archive.importing():
        return JsonResponse({'error': 'An import is already running.'}, status=409)
    busy = archive.busy_jobs()
    if busy:
        return JsonResponse({'error': '%d job%s queued or running. Wait for them to finish or stop them, then import.' % (busy, '' if busy == 1 else 's')}, status=409)
    try:
        name = archive.keep_upload(upload)
    except archive.ArchiveError as error:
        return JsonResponse({'error': str(error)}, status=400)
    archive.set_status(state='queued', file=str(upload.name)[:200])
    try:
        tasks.restore_database.delay(name, str(upload.name)[:200])
    except Exception:
        (archive.folder() / name).unlink(missing_ok=True)
        archive.set_status(state='failed', file=str(upload.name)[:200], error='The import could not be queued. Nothing was changed.')
        return JsonResponse({'error': 'The import could not be queued. Nothing was changed.'}, status=503)
    return JsonResponse(_overview(), status=202)
