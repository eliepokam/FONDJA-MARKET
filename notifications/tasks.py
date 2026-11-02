import logging

from celery import shared_task
from django.conf import settings

from users.models import Utilisateur
from .services import send_push_notification

logger = logging.getLogger('django')


@shared_task
def envoyer_push_task(utilisateur_id, title, body, link=''):
    """Envoie une notification push en arrière-plan. Ne doit jamais faire échouer l'action qui l'a déclenchée."""
    if getattr(settings, 'DISABLE_EXTERNAL_MESSAGING', False):
        return
    utilisateur = Utilisateur.objects.filter(pk=utilisateur_id).first()
    if utilisateur is None:
        return
    try:
        send_push_notification(utilisateur, title, body, link)
    except Exception as exc:   # Firebase non configuré, jeton périmé, réseau...
        logger.warning(f"Push non envoyé à l'utilisateur {utilisateur_id} : {exc}")
