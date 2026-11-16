import os
from celery import Celery

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'leakscope.settings')
app = Celery('leakscope')
app.config_from_object('django.conf:settings', namespace='CELERY')
app.autodiscover_tasks()


@app.task(bind=True)
def debug_task(self):
    """Harmless broker/worker round-trip used by deployment verification."""
    return {'ok': True}
