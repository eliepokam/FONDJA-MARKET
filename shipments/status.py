"""
Changement de statut d'un colis : UNE seule implémentation, utilisée par l'écran unitaire ET par l'action groupée.

Avant, deux copies divergeaient : l'une ne notifiait pas en push, l'autre prenait un tarif au hasard pour la date
estimée, et aucune ne contrôlait le sens du changement (un colis livré pouvait repasser à « déclaré »).
"""
from datetime import timedelta

from django.db import transaction
from django.utils import timezone

from notifications.models import Notification
from notifications.services import notify
from tracking.models import HistoriqueStatutColis
from .models import Colis
from .pricing import tarif_actif

# Ordre réel du parcours d'un colis (c'est l'ordre de déclaration de l'énumération).
ORDRE = [valeur for valeur, _ in Colis.Statut.choices]


# Qui peut enregistrer quelle étape. Un agent Chine s'occupe de la réception et du départ ; un agent Cameroun, de
# l'arrivée, de la douane et de la livraison. « En transit » peut être confirmé des deux côtés (le colis est entre les deux).
# C'est LE seul endroit à modifier si ta règle change : le serveur la fait respecter et la communique au front.
STATUTS_PAR_LOCALISATION = {
    'chine': ['recu_entrepot', 'embarque', 'en_transit'],
    'cameroun': ['en_transit', 'dedouanement', 'arrive_depot_local', 'en_livraison', 'livre'],
}


def statuts_autorises(utilisateur):
    """Étapes que cet utilisateur peut enregistrer. Un administrateur peut tout enregistrer (et corriger en arrière)."""
    if hasattr(utilisateur, 'administrateur'):
        return list(ORDRE)
    agent = getattr(utilisateur, 'agent', None)
    if agent is None:
        return []
    return list(STATUTS_PAR_LOCALISATION.get(agent.localisation, []))


class TransitionInvalide(Exception):
    """Le changement de statut demandé est refusé ; le message est destiné à l'utilisateur."""


def verifier_transition(colis, nouveau_statut, utilisateur):
    if nouveau_statut not in ORDRE:
        raise TransitionInvalide("Statut inconnu.")
    if nouveau_statut == colis.statut:
        raise TransitionInvalide(f"Le colis est déjà « {colis.get_statut_display()} ».")
    if nouveau_statut not in statuts_autorises(utilisateur):
        agent = getattr(utilisateur, 'agent', None)
        entrepot = {'chine': "de l'entrepôt Chine", 'cameroun': 'du dépôt Cameroun'}.get(getattr(agent, 'localisation', ''), "de votre site")
        raise TransitionInvalide(
            f"Les agents {entrepot} ne peuvent pas enregistrer l'étape « {dict(Colis.Statut.choices)[nouveau_statut]} » : "
            f"elle est réservée à l'autre site (ou à un administrateur)."
        )
    recule = ORDRE.index(nouveau_statut) < ORDRE.index(colis.statut)
    # Un agent fait avancer les colis ; revenir en arrière (ex. un colis livré) est une correction réservée aux administrateurs.
    if recule and not hasattr(utilisateur, 'administrateur'):
        raise TransitionInvalide(
            f"Un agent ne peut pas faire reculer un colis (de « {colis.get_statut_display()} » vers "
            f"« {dict(Colis.Statut.choices)[nouveau_statut]} »). Demande à un administrateur."
        )


def changer_statut(colis, nouveau_statut, utilisateur, note=''):
    """Applique le changement (historique, date estimée, notification) ou lève TransitionInvalide."""
    verifier_transition(colis, nouveau_statut, utilisateur)
    agent = utilisateur.agent if hasattr(utilisateur, 'agent') else None

    with transaction.atomic():
        HistoriqueStatutColis.objects.create(colis=colis, statut=nouveau_statut, agent=agent, note=note or None)
        colis.statut = nouveau_statut
        champs = ['statut', 'modifie_le']

        # Le délai de livraison démarre à l'embarquement ; on prend le tarif ACTIF le plus récent (déterministe).
        if nouveau_statut == Colis.Statut.EMBARQUE and not colis.date_livraison_estimee:
            tarif = tarif_actif(colis.mode_transport)
            if tarif and tarif.delai_max_jours:
                colis.date_livraison_estimee = timezone.localdate() + timedelta(days=tarif.delai_max_jours)
                champs.append('date_livraison_estimee')

        colis.save(update_fields=champs)
        notify(
            colis.client,
            f"Votre colis {colis.code_suivi} est maintenant : {colis.get_statut_display()}",
            Notification.Type.STATUT_COLIS,
            colis=colis,
        )
    return colis
