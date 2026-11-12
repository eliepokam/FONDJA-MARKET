"""
Conteneurs (lots d'embarquement) : remplissage, évolution, clôture.

Pourcentage de remplissage = volume (m³) des colis du conteneur / capacité utile. Le volume d'un colis est celui MESURÉ par l'agent, à défaut celui
DÉCLARÉ par le client (même règle que le tableau de bord). Chaque changement de remplissage est photographié (RemplissageLot) : c'est la courbe
d'évolution affichée sur le tableau de bord.
"""
from datetime import timedelta
from decimal import Decimal

from django.db.models import Sum
from django.db.models.functions import Coalesce
from django.utils import timezone

from .models import Colis, LotDedouanement, RemplissageLot

DEUX = Decimal('0.01')


def volume_du_lot(lot):
    total = Colis.objects.filter(lot=lot).aggregate(v=Sum(Coalesce('volume_m3', 'volume_declare_m3')))['v']
    return Decimal(total or 0)


def pourcentage(volume, capacite):
    if not capacite:
        return Decimal('0')
    return (Decimal(volume) / Decimal(capacite) * 100).quantize(DEUX)


def etat(lot):
    """Remplissage actuel du conteneur, calculé à la demande."""
    volume = volume_du_lot(lot)
    return {'volume_m3': volume, 'pourcentage': pourcentage(volume, lot.capacite_m3), 'nb_colis': Colis.objects.filter(lot=lot).count()}


def enregistrer_remplissage(lot, forcer=False):
    """Ajoute une photographie si le remplissage a changé depuis la précédente (ou si `forcer`). Retourne la photographie créée, sinon None."""
    e = etat(lot)
    derniere = lot.remplissages.order_by('-cree_le', '-id').first()
    if derniere and not forcer and (derniere.volume_m3, derniere.pourcentage, derniere.nb_colis) == (e['volume_m3'].quantize(Decimal('0.001')), e['pourcentage'], e['nb_colis']):
        return None
    return RemplissageLot.objects.create(lot=lot, **e)


def enregistrer_pour(ids_de_lots, forcer=False):
    for lot in LotDedouanement.objects.filter(pk__in={i for i in ids_de_lots if i}):
        enregistrer_remplissage(lot, forcer=forcer)


def evolution(lot, limite=30):
    """Les `limite` dernières photographies, de la plus ancienne à la plus récente (pour tracer la courbe)."""
    points = list(lot.remplissages.order_by('-cree_le', '-id')[:limite])[::-1]
    return [{'date': p.cree_le, 'pourcentage': p.pourcentage, 'volume_m3': p.volume_m3, 'nb_colis': p.nb_colis} for p in points]


def variation(lot, jours=7):
    """Points de pourcentage gagnés (ou perdus) sur la période : pourcentage actuel − pourcentage à la fin de la période précédente (0 % si le conteneur est plus récent)."""
    limite = timezone.now() - timedelta(days=jours)
    avant = lot.remplissages.filter(cree_le__lte=limite).order_by('-cree_le', '-id').first()
    actuel = pourcentage(volume_du_lot(lot), lot.capacite_m3)
    return actuel - (avant.pourcentage if avant else Decimal('0'))
