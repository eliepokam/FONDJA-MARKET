"""
Suppression de compte CLIENT (droit à l'effacement) : on efface les données d'identification, on garde les justificatifs.

Un client qui a des colis ou des paiements ne peut pas être supprimé de la base : ces enregistrements sont des pièces comptables
et douanières. Le compte est donc ANONYMISÉ : nom, téléphone, e-mail, mot de passe, code client, jetons de notification, codes
de vérification et notifications sont effacés ; colis, paiements et réclamations restent, rattachés à un compte « supprimé ».
Le numéro de téléphone est libéré : la personne peut se réinscrire plus tard avec le même numéro.
"""
import logging

from django.db import transaction
from django.utils import timezone

from core.models import JournalActivite
from notifications.models import FCMToken, Notification
from payments.models import Paiement
from shipments.models import Colis
from .models import Client, CodeOtp, Utilisateur

logger = logging.getLogger('django')

LIBELLE_SUPPRIME = "Compte supprimé"


class SuppressionImpossible(Exception):
    """La suppression est refusée pour l'instant ; `obstacles` explique pourquoi, en français."""

    def __init__(self, obstacles):
        self.obstacles = obstacles
        super().__init__(message_obstacles(obstacles))


def message_obstacles(obstacles):
    return ("Impossible de supprimer le compte pour le moment : " + ", ".join(obstacles)
            + ". Terminez ou réglez ces éléments d'abord.")


def obstacles_suppression(client):
    """Ce qui empêche de supprimer ce compte aujourd'hui (liste vide = suppression possible)."""
    obstacles = []
    en_cours = Colis.objects.filter(client=client).exclude(statut=Colis.Statut.LIVRE).count()
    if en_cours:
        obstacles.append(f"{en_cours} colis en cours d'acheminement" if en_cours > 1 else "1 colis en cours d'acheminement")

    en_attente = Paiement.objects.filter(client=client, statut=Paiement.Statut.EN_ATTENTE).count()
    if en_attente:
        obstacles.append(f"{en_attente} paiements en attente" if en_attente > 1 else "1 paiement en attente")

    # Colis livré dont le coût n'a jamais été réglé : supprimer le compte ferait disparaître une créance.
    impayes = (Colis.objects.filter(client=client, statut=Colis.Statut.LIVRE, cout_final__gt=0)
               .exclude(paiements__statut=Paiement.Statut.PAYE).distinct().count())
    if impayes:
        obstacles.append(f"{impayes} colis livrés non réglés" if impayes > 1 else "1 colis livré non réglé")
    return obstacles


def a_des_enregistrements_comptables(client):
    return Colis.objects.filter(client=client).exists() or Paiement.objects.filter(client=client).exists()


@transaction.atomic
def anonymiser_client(client):
    """Efface les données d'identification du client. Lève SuppressionImpossible s'il reste des colis en cours ou des impayés."""
    from .views import blacklist_all_tokens   # import tardif : views importe ce module

    client = Client.objects.select_for_update().get(pk=client.pk)
    obstacles = obstacles_suppression(client)
    if obstacles:
        raise SuppressionImpossible(obstacles)

    ancien_nom, ancien_telephone = client.nom, str(client.telephone)

    # Données qui n'ont plus aucune raison d'exister
    Notification.objects.filter(utilisateur=client).delete()
    FCMToken.objects.filter(utilisateur=client).delete()
    CodeOtp.objects.filter(utilisateur=client).delete()

    # Le numéro Mobile Money d'un paiement est une donnée personnelle ; la référence de transaction, elle, suffit à la comptabilité.
    Paiement.objects.filter(client=client).update(telephone_paiement=None)

    # Traces du nom ou du numéro dans les textes destinés au personnel
    for notification in Notification.objects.filter(colis__client=client, contenu__contains=f"de {ancien_nom} :"):
        notification.contenu = notification.contenu.replace(f"de {ancien_nom} :", f"de {LIBELLE_SUPPRIME} :")
        notification.save(update_fields=['contenu'])
    JournalActivite.objects.filter(cible_description__contains=ancien_telephone).update(cible_description=LIBELLE_SUPPRIME, details={})

    client.nom = LIBELLE_SUPPRIME
    client.telephone = f"SUPPRIME-{client.pk}"          # libère le vrai numéro (champ unique)
    client.email = None
    client.code_client = f"SUPPRIME-{client.pk}"
    client.set_unusable_password()
    client.statut = Utilisateur.Statut.DESACTIVE
    client.telephone_verifie_le = None
    client.jeton_connexion = None
    client.tentatives_connexion_echouees = 0
    client.bloque_jusqu_a = None
    client.anonymise_le = timezone.now()
    client.save()

    blacklist_all_tokens(client)                          # ferme toutes les sessions ouvertes, sur tous les appareils
    logger.info(f"Compte client #{client.pk} anonymisé")  # sans aucune donnée personnelle
    return client
