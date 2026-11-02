import logging

from django.db import transaction
from django.utils import timezone
from firebase_admin import messaging
from .models import FCMToken, Notification


def send_push_notification(utilisateur, title: str, body: str, link: str = ""):
    tokens = list(FCMToken.objects.filter(utilisateur=utilisateur).values_list('token', flat=True))
    if not tokens:
        return

    message = messaging.MulticastMessage(
        notification=messaging.Notification(title=title, body=body),
        data={'link': link},
        tokens=tokens,
    )
    response = messaging.send_each_for_multicast(message)

    # Nettoyage : un token invalide (app désinstallée, permission révoquée...) doit être supprimé
    for token, resp in zip(tokens, response.responses):
        if not resp.success:
            FCMToken.objects.filter(token=token).delete()


logger = logging.getLogger('django')

TITRES_PUSH = {
    Notification.Type.STATUT_COLIS: "Mise à jour de votre colis",
    Notification.Type.PAIEMENT: "Paiement",
    Notification.Type.RECLAMATION: "Réclamation",
    Notification.Type.INCIDENT: "Imprévu sur votre colis",
}


def notify(utilisateur, message: str, type_: str, colis=None, canal=Notification.Canal.PUSH):
    notification = Notification.objects.create(
        utilisateur=utilisateur,
        colis=colis,
        type=type_,
        canal=canal,
        contenu=message,
        envoye_le=timezone.now(),
    )
    if canal == Notification.Canal.PUSH:
        _programmer_push(utilisateur.pk, TITRES_PUSH.get(type_, "Fondja Market"), message,
                         f"/shipments/{colis.pk}" if colis else "/notifications")
    return notification


def _programmer_push(utilisateur_id, titre, corps, lien):
    """Push en arrière-plan, après validation de la transaction, et sans jamais bloquer l'action en cours."""
    from .tasks import envoyer_push_task

    def envoyer():
        try:
            envoyer_push_task.delay(utilisateur_id, titre, corps, lien)
        except Exception as exc:   # broker Celery indisponible : la notification reste visible dans l'application
            logger.warning(f"Push non programmé pour l'utilisateur {utilisateur_id} : {exc}")

    transaction.on_commit(envoyer)


def notify_staff(message: str, type_: str, colis=None):
    """Notifie tous les agents et administrateurs — utile tant qu'aucune réclamation
    n'est encore assignée à une personne précise."""
    from users.models import Agent, Administrateur
    for agent in Agent.objects.all():
        notify(agent, message, type_, colis=colis)
    for admin in Administrateur.objects.all():
        notify(admin, message, type_, colis=colis)