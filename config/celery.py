import os
from celery import Celery

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')

app = Celery('config')
app.config_from_object('django.conf:settings', namespace='CELERY')
app.autodiscover_tasks()

# Planification (processus « celery -A config beat ») : purge quotidienne des données à 3 h, heure du Cameroun (CELERY_TIMEZONE).
from celery.schedules import crontab  # noqa: E402

app.conf.beat_schedule = {
    'purge-quotidienne-des-donnees': {
        'task': 'core.tasks.purger_donnees_task',
        'schedule': crontab(hour=3, minute=0),
    },
}
