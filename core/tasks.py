import logging

from celery import shared_task

from .retention import purger_donnees

logger = logging.getLogger('django')


@shared_task
def purger_donnees_task():
    """Purge quotidienne (planifiée à 3 h, heure du Cameroun, dans config/celery.py)."""
    return purger_donnees()
