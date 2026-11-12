import logging

from django.db import transaction
from django.utils import timezone
from firebase_admin import messaging
from .models import FCMToken, Notification


def firebase_pret():
    """Vrai si Firebase est initialisé côté serveur (fichier d'identifiants valide). Sinon aucune notification push ne peut partir."""
    import firebase_admin
    return bool(firebase_admin._apps)


def _explication_echec(exc):
    """Explication en français d'un échec d'envoi à UN appareil. Retourne (texte, jeton_a_supprimer)."""
    from firebase_admin import exceptions as fb
    if isinstance(exc, messaging.UnregisteredError):
        return "jeton périmé (application désinstallée, navigateur réinitialisé ou autorisation retirée) : supprimé", True
    if isinstance(exc, messaging.SenderIdMismatchError):
        return ("ce jeton a été créé avec un AUTRE projet Firebase que celui des identifiants du serveur : le projet du front "
                "(projectId, messagingSenderId, clé VAPID) et le fichier d'identifiants du serveur doivent être ceux du MÊME projet : supprimé"), True
    if isinstance(exc, fb.InvalidArgumentError):
        return "jeton invalide : supprimé", True
    if isinstance(exc, messaging.ThirdPartyAuthError):
        return "Firebase refuse l'authentification Web Push (clé VAPID ou identifiants incorrects) : jeton conservé", False
    if isinstance(exc, messaging.QuotaExceededError):
        return "quota d'envoi Firebase dépassé : jeton conservé, réessayez plus tard", False
    if isinstance(exc, (fb.UnavailableError, fb.InternalError, fb.DeadlineExceededError)):
        return "Firebase momentanément indisponible : jeton conservé", False
    return f"erreur Firebase ({exc.__class__.__name__}) : jeton conservé", False


def send_push_notification(utilisateur, title: str, body: str, link: str = ""):
    """
    Envoie une notification push à tous les appareils de l'utilisateur. Retourne un résumé :
      {'tokens': n, 'envoyes': k, 'supprimes': d, 'erreurs': [{'jeton': '…abcd', 'raison': '…'}]}
    Un jeton n'est supprimé que si Firebase dit qu'il est définitivement inutilisable (appareil désinstallé, mauvais projet…) :
    une panne passagère (Firebase indisponible, quota) ne doit PAS faire perdre les appareils d'un client.
    """
    from django.conf import settings

    tokens = list(FCMToken.objects.filter(utilisateur=utilisateur).values_list('token', flat=True))
    resume = {'tokens': len(tokens), 'envoyes': 0, 'supprimes': 0, 'erreurs': []}
    if not tokens:
        return resume

    # Adresse ABSOLUE du site : sans elle, un clic sur la notification ouvre la page d'accueil au lieu du colis concerné.
    base = str(getattr(settings, 'FRONT_URL', '') or '').rstrip('/')
    # Message « web push » : durée de vie 24 h (un colis expédié ne doit pas notifier trois jours plus tard) et priorité haute.
    en_tetes = {'TTL': '86400', 'Urgency': 'high'}
    webpush = messaging.WebpushConfig(headers=en_tetes)
    if base:
        webpush = messaging.WebpushConfig(
            headers=en_tetes,
            notification=messaging.WebpushNotification(icon=f"{base}/icons/icon-192.png", badge=f"{base}/icons/badge-96.png"),
            fcm_options=messaging.WebpushFCMOptions(link=f"{base}{link}" if link.startswith('/') else base),
        )

    message = messaging.MulticastMessage(
        notification=messaging.Notification(title=title, body=body),
        data={'link': link},
        webpush=webpush,
        tokens=tokens,
    )
    reponse = messaging.send_each_for_multicast(message)

    for token, resp in zip(tokens, reponse.responses):
        if resp.success:
            resume['envoyes'] += 1
            continue
        raison, supprimer = _explication_echec(resp.exception)
        if supprimer:
            FCMToken.objects.filter(token=token).delete()
            resume['supprimes'] += 1
        resume['erreurs'].append({'jeton': f"…{token[-6:]}", 'raison': raison})
        logging.getLogger('django').warning(f"[Push] utilisateur {utilisateur.pk}, appareil …{token[-6:]} : {raison}")
    return resume


logger = logging.getLogger('django')

TITRES_PUSH = {
    Notification.Type.STATUT_COLIS: "Mise à jour de votre colis",
    Notification.Type.PAIEMENT: "Paiement",
    Notification.Type.RECLAMATION: "Réclamation",
    Notification.Type.INCIDENT: "Imprévu sur votre colis",
}

# Pages de chaque espace. Avant, TOUTES les notifications liées à un colis pointaient vers /shipments/<id> (espace client) :
# un agent ou un administrateur qui cliquait était renvoyé vers l'interface client.
PAGES = {
    'notifications': {'client': '/notifications', 'agent': '/agent/notifications', 'admin': '/admin/notifications'},
    'virements': {'client': '/notifications', 'agent': '/agent/virements', 'admin': '/admin/virements'},
    'comptes': {'client': '/notifications', 'agent': '/agent/notifications', 'admin': '/admin/users'},
}


def espace_de(utilisateur):
    """« admin », « agent » ou « client » : l'espace du site où ce compte travaille."""
    if hasattr(utilisateur, 'administrateur'):
        return 'admin'
    if hasattr(utilisateur, 'agent'):
        return 'agent'
    return 'client'


def lien_pour(utilisateur, colis=None, reclamation=None, page=None):
    """Adresse (dans le BON espace) que la notification ouvre pour ce destinataire."""
    espace = espace_de(utilisateur)
    if page:
        return PAGES.get(page, PAGES['notifications'])[espace]
    if reclamation is not None:
        return {'client': f"/complaints/{reclamation.pk}", 'agent': f"/agent/complaints/{reclamation.pk}",
                'admin': f"/admin/complaints/{reclamation.pk}"}[espace]
    if colis is not None:
        return {'client': f"/shipments/{colis.pk}", 'agent': f"/agent/shipments/{colis.pk}", 'admin': f"/admin/colis/{colis.pk}"}[espace]
    return PAGES['notifications'][espace]


def notify(utilisateur, message: str, type_: str, colis=None, canal=Notification.Canal.PUSH, reclamation=None, page=None):
    """
    Crée la notification (visible dans l'application) et, pour le canal push, programme l'envoi sur les appareils du destinataire.
    `reclamation` / `page` ('virements', 'comptes') précisent la page à ouvrir ; sinon le colis, sinon la liste des notifications.
    """
    lien = lien_pour(utilisateur, colis=colis, reclamation=reclamation, page=page)
    notification = Notification.objects.create(
        utilisateur=utilisateur,
        colis=colis,
        type=type_,
        canal=canal,
        contenu=message,
        lien=lien,
        envoye_le=timezone.now(),
    )
    if canal == Notification.Canal.PUSH:
        _programmer_push(utilisateur.pk, TITRES_PUSH.get(type_, "Fondja Market"), message, lien)
    return notification


def _programmer_push(utilisateur_id, titre, corps, lien):
    """Push en arrière-plan, après validation de la transaction, sans jamais bloquer ni faire échouer l'action en cours."""
    from core.taches import lancer_tache
    from .tasks import envoyer_push_task
    try:
        lancer_tache(envoyer_push_task, utilisateur_id, titre, corps, lien)
    except Exception as exc:   # ne doit jamais casser l'action métier ; la notification reste visible dans l'application
        logger.warning(f"Push non programmé pour l'utilisateur {utilisateur_id} : {exc}")


def notify_staff(message: str, type_: str, colis=None, reclamation=None, page=None):
    """Notifie tous les agents et administrateurs (chacun reçoit le lien de SON espace)."""
    from users.models import Agent, Administrateur
    for agent in Agent.objects.all():
        notify(agent, message, type_, colis=colis, reclamation=reclamation, page=page)
    for admin in Administrateur.objects.all():
        notify(admin, message, type_, colis=colis, reclamation=reclamation, page=page)
