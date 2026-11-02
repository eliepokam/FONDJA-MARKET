"""
Calcul AUTOMATIQUE du coût final d'un colis, à partir des mesures confirmées par l'agent.

Règle reprise de ton implémentation existante (ColisAgentSerializer) :
  - maritime  -> volume_m3 x prix_par_m3 (repli sur poids_kg x prix_par_kg si le volume ou le prix au m³ manque) ;
  - autres    -> poids_kg x prix_par_kg.
Ajouts : tarif ACTIF le plus récent (déterministe), arrondi à l'XAF entier, refus d'un coût nul
ou d'un tarif hors XAF, et erreur explicite au lieu d'un échec silencieux.
"""
from decimal import Decimal, ROUND_HALF_UP

from .models import Colis, Tarif


class CoutIndisponible(Exception):
    """Le coût ne peut pas être calculé (tarif absent, mesure manquante...)."""


def tarif_actif(mode_transport):
    return (
        Tarif.objects.filter(mode_transport=mode_transport, actif=True)
        .order_by('-cree_le', '-pk')
        .first()
    )


def calculer_cout(colis):
    tarif = tarif_actif(colis.mode_transport)
    if tarif is None:
        raise CoutIndisponible("Aucun tarif actif pour ce mode de transport.")
    if tarif.devise != 'XAF':
        raise CoutIndisponible(f"Devise de tarif non supportée : {tarif.devise}.")

    cout = None
    if colis.mode_transport == Colis.ModeTransport.MARITIME and colis.volume_m3 and tarif.prix_par_m3:
        cout = colis.volume_m3 * tarif.prix_par_m3
    elif colis.poids_kg and tarif.prix_par_kg:
        cout = colis.poids_kg * tarif.prix_par_kg

    if cout is None:
        raise CoutIndisponible("Mesure ou prix manquant : impossible d'appliquer le tarif actif.")

    cout = cout.quantize(Decimal('1'), rounding=ROUND_HALF_UP)
    if cout <= 0:
        raise CoutIndisponible("Le coût calculé est nul : vérifiez les mesures et le tarif.")
    return cout
