"""
Purge planifiée des données : ne garder que ce qui est utile, et seulement le temps prévu par la politique de confidentialité.

Chaque règle est indépendante, idempotente (relancer la purge ne supprime rien de plus) et sait faire une simulation (`dry_run`).
Les durées se règlent par la variable RETENTION des réglages ; ce qui dépend d'une obligation légale est DÉSACTIVÉ par défaut
tant qu'un juriste n'a pas confirmé la durée (PIECES_COMPTABLES_ANNEES = 0).

  Règle                                   Défaut         Effet
  ──────────────────────────────────────  ─────────────  ─────────────────────────────────────────────────────────────
  Codes de vérification (OTP)             1 jour         supprimés (valables 5 minutes, inutiles ensuite)
  Jetons de session expirés               toujours       supprimés (table de révocation de simplejwt)
  Notifications                           365 jours      supprimées
  Comptes clients jamais vérifiés         30 jours       supprimés s'ils n'ont ni colis ni paiement
  Pièces comptables d'un compte supprimé  désactivé      colis, paiements, réclamations, photos et le compte anonyme lui-même
                                                         supprimés N années après la suppression du compte

Non traité volontairement : l'anonymisation automatique des comptes INACTIFS. Elle suppose une mesure fiable de l'activité
(la dernière connexion n'était pas enregistrée jusqu'ici) et un préavis à la personne ; la supprimer à tort est irréversible.
"""
import logging
from datetime import timedelta

from django.conf import settings
from django.db import transaction
from django.db.models.deletion import ProtectedError, RestrictedError
from django.utils import timezone
from rest_framework_simplejwt.token_blacklist.models import OutstandingToken

from complaints.models import PhotoMessageReclamation, PhotoReclamation, Reclamation
from notifications.models import Notification
from payments.models import Paiement
from shipments.models import Colis
from tracking.models import PhotoColis
from users.models import Client, CodeOtp
from .models import JournalActivite

logger = logging.getLogger('django')

DEFAUTS = {
    'OTP_JOURS': 1,
    'NOTIFICATIONS_JOURS': 365,
    'COMPTES_NON_VERIFIES_JOURS': 30,
    'PIECES_COMPTABLES_ANNEES': 0,       # 0 = désactivé : durée légale à confirmer avant d'activer
}


def reglages():
    valeurs = dict(DEFAUTS)
    valeurs.update(getattr(settings, 'RETENTION', None) or {})
    return valeurs


def _avant(maintenant, jours):
    return maintenant - timedelta(days=jours)


def purger_codes_otp(maintenant, dry_run=False):
    qs = CodeOtp.objects.filter(cree_le__lt=_avant(maintenant, reglages()['OTP_JOURS']))
    n = qs.count()
    if not dry_run:
        qs.delete()
    return n


def purger_jetons_expires(maintenant, dry_run=False):
    """Équivalent de `manage.py flushexpiredtokens` : la table des jetons grossit à chaque connexion et ne se vide jamais seule."""
    qs = OutstandingToken.objects.filter(expires_at__lt=maintenant)
    n = qs.count()
    if not dry_run:
        qs.delete()          # supprime aussi les entrées de la liste noire qui leur sont liées
    return n


def purger_notifications(maintenant, dry_run=False):
    qs = Notification.objects.filter(cree_le__lt=_avant(maintenant, reglages()['NOTIFICATIONS_JOURS']))
    n = qs.count()
    if not dry_run:
        qs.delete()
    return n


def purger_comptes_non_verifies(maintenant, dry_run=False):
    """Comptes ouverts mais jamais confirmés par code, sans aucun colis ni paiement : ils n'ont plus de raison d'exister."""
    candidats = (Client.objects
                 .filter(telephone_verifie_le__isnull=True, anonymise_le__isnull=True,
                         cree_le__lt=_avant(maintenant, reglages()['COMPTES_NON_VERIFIES_JOURS']))
                 .filter(colis__isnull=True, paiements__isnull=True, reclamations__isnull=True)
                 .distinct())
    supprimes = 0
    for client in candidats:
        if dry_run:
            supprimes += 1
            continue
        try:
            client.delete()
            supprimes += 1
        except (RestrictedError, ProtectedError):
            logger.warning(f"Compte non vérifié #{client.pk} conservé : des données y sont rattachées")
    return supprimes


def _supprimer_fichiers(*querysets):
    """Supprime les fichiers du disque : effacer la ligne en base ne supprime PAS l'image ou la vidéo stockée."""
    n = 0
    for qs in querysets:
        for media in qs.iterator():
            if media.url:
                media.url.delete(save=False)
                n += 1
    return n


def purger_pieces_comptables(maintenant, dry_run=False):
    """
    Supprime définitivement ce qui a été conservé (sans identité) pour la comptabilité, une fois la durée écoulée depuis la
    suppression du compte. Ne touche JAMAIS à un compte actif ni à ses colis. Retourne (comptes, fichiers).
    """
    annees = reglages()['PIECES_COMPTABLES_ANNEES']
    if not annees:
        return 0, 0
    limite = maintenant - timedelta(days=round(365.25 * annees))
    comptes = fichiers = 0
    for client in Client.objects.filter(anonymise_le__isnull=False, anonymise_le__lt=limite):
        medias = (PhotoColis.objects.filter(colis__client=client),
                  PhotoReclamation.objects.filter(reclamation__client=client),
                  PhotoMessageReclamation.objects.filter(message__reclamation__client=client))
        if dry_run:
            comptes += 1
            fichiers += sum(qs.count() for qs in medias)
            continue
        with transaction.atomic():
            fichiers += _supprimer_fichiers(*medias)
            Paiement.objects.filter(client=client).delete()
            Reclamation.objects.filter(client=client).delete()      # messages et photos suivent en cascade
            Colis.objects.filter(client=client).delete()            # historique, photos, incidents suivent en cascade
            client.delete()
            comptes += 1
    return comptes, fichiers


def purger_donnees(maintenant=None, dry_run=False):
    """Applique toutes les règles. Retourne le nombre d'éléments supprimés (ou qui le seraient, en simulation) par règle."""
    maintenant = maintenant or timezone.now()
    comptes, fichiers = purger_pieces_comptables(maintenant, dry_run)
    resultat = {
        'codes_otp': purger_codes_otp(maintenant, dry_run),
        'jetons_expires': purger_jetons_expires(maintenant, dry_run),
        'notifications': purger_notifications(maintenant, dry_run),
        'comptes_non_verifies': purger_comptes_non_verifies(maintenant, dry_run),
        'pieces_comptables_comptes': comptes,
        'pieces_comptables_fichiers': fichiers,
    }
    if not dry_run and any(resultat.values()):
        # Trace d'audit de l'action automatique (auteur vide = « Système » dans le journal d'administration)
        JournalActivite.objects.create(auteur=None, action=JournalActivite.Action.PURGE_DONNEES,
                                       cible_description="Purge automatique des données", details=resultat)
        logger.info(f"Purge des données : {resultat}")
    return resultat
