from __future__ import absolute_import
import os
from celery import Celery
from django.conf import settings

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'turi_check.settings')

app = Celery('turi_check')

app.config_from_object('django.conf:settings', namespace='CELERY')

app.conf.update(
    broker_url=os.environ.get('CELERY_BROKER_URL'),
    result_backend=os.environ.get('CELERY_RESULT_BACKEND'),
    worker_send_task_events=True,
    task_send_sent_event=True,
)

app.autodiscover_tasks(lambda: settings.INSTALLED_APPS)

@app.task(bind=True)
def debug_task(self):
    print(f'Request: {self.request!r}')