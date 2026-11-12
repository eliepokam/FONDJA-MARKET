"""Chiffre d'affaires = somme des paiements ENCAISSÉS (statut « payé »), à la date de leur encaissement (paye_le).
Un virement compte à la date où un agent ou un administrateur l'a VALIDÉ, pas à celle où le client l'a déclaré."""
import calendar
from datetime import date
from decimal import Decimal

from django.db.models import Count, Sum
from django.db.models.functions import TruncDay, TruncMonth, TruncYear
from django.utils import timezone

from .models import Paiement

MOIS = ['janvier', 'février', 'mars', 'avril', 'mai', 'juin', 'juillet', 'août', 'septembre', 'octobre', 'novembre', 'décembre']
LIBELLES_METHODES = {code: libelle for code, libelle in Paiement.Methode.choices}


def _debut(jour: date):
    return timezone.make_aware(timezone.datetime(jour.year, jour.month, jour.day))


def _paiements():
    return Paiement.objects.filter(statut=Paiement.Statut.PAYE, paye_le__isnull=False)


def _total(qs):
    return qs.aggregate(t=Sum('montant'))['t'] or Decimal('0')


def _bornes_mois(annee, mois):
    suivant = date(annee + (mois == 12), 1 if mois == 12 else mois + 1, 1)
    return _debut(date(annee, mois, 1)), _debut(suivant)


def _bornes_annee(annee):
    return _debut(date(annee, 1, 1)), _debut(date(annee + 1, 1, 1))


def _variation(courant, precedent):
    if precedent is None or precedent == 0:
        return None
    return round(float((courant - precedent) / precedent * 100), 1)


def chiffre_affaires(periode, annee=None, mois=None):
    """periode : 'mois' (annee + mois), 'annee' (annee) ou 'global'. Retourne le total, la répartition par moyen de paiement,
    la série pour le graphique (jours / mois / années, zéros compris) et la comparaison avec la période précédente."""
    maintenant = timezone.localtime()
    qs, precedent, libelle_precedent = _paiements(), None, None

    if periode == 'mois':
        annee, mois = annee or maintenant.year, mois or maintenant.month
        debut, fin = _bornes_mois(annee, mois)
        qs = qs.filter(paye_le__gte=debut, paye_le__lt=fin)
        libelle = f"{MOIS[mois - 1]} {annee}"
        pa, pm = (annee - 1, 12) if mois == 1 else (annee, mois - 1)
        d2, f2 = _bornes_mois(pa, pm)
        precedent = _total(_paiements().filter(paye_le__gte=d2, paye_le__lt=f2))
        libelle_precedent = f"{MOIS[pm - 1]} {pa}"
        nb_jours = calendar.monthrange(annee, mois)[1]
        par_jour = {timezone.localtime(r['p']).day: r['t'] for r in qs.annotate(p=TruncDay('paye_le')).values('p').annotate(t=Sum('montant'))}
        serie = [{'cle': f"{annee}-{mois:02d}-{j:02d}", 'libelle': str(j), 'total': float(par_jour.get(j, 0))} for j in range(1, nb_jours + 1)]
    elif periode == 'annee':
        annee = annee or maintenant.year
        debut, fin = _bornes_annee(annee)
        qs = qs.filter(paye_le__gte=debut, paye_le__lt=fin)
        libelle = str(annee)
        d2, f2 = _bornes_annee(annee - 1)
        precedent = _total(_paiements().filter(paye_le__gte=d2, paye_le__lt=f2))
        libelle_precedent = str(annee - 1)
        par_mois = {timezone.localtime(r['p']).month: r['t'] for r in qs.annotate(p=TruncMonth('paye_le')).values('p').annotate(t=Sum('montant'))}
        serie = [{'cle': f"{annee}-{m:02d}", 'libelle': MOIS[m - 1][:3] + '.', 'total': float(par_mois.get(m, 0))} for m in range(1, 13)]
    else:   # global
        libelle = "Depuis le début"
        par_annee = {timezone.localtime(r['p']).year: r['t'] for r in qs.annotate(p=TruncYear('paye_le')).values('p').annotate(t=Sum('montant'))}
        premiere = min(par_annee) if par_annee else maintenant.year
        serie = [{'cle': str(a), 'libelle': str(a), 'total': float(par_annee.get(a, 0))} for a in range(premiere, maintenant.year + 1)]

    total = _total(qs)
    par_methode = [
        {'methode': r['methode'], 'libelle': LIBELLES_METHODES.get(r['methode'], r['methode']), 'total': float(r['t']), 'nb': r['n']}
        for r in qs.values('methode').annotate(t=Sum('montant'), n=Count('id')).order_by('-t')
    ]
    premiere_annee = _paiements().order_by('paye_le').values_list('paye_le', flat=True).first()
    return {
        'periode': periode, 'libelle': libelle, 'total': float(total), 'nb_paiements': qs.count(),
        'par_methode': par_methode, 'serie': serie,
        'precedent': None if precedent is None else {'libelle': libelle_precedent, 'total': float(precedent)},
        'variation_pct': _variation(total, precedent),
        'premiere_annee': timezone.localtime(premiere_annee).year if premiere_annee else maintenant.year,
        'annee': annee, 'mois': mois,
    }
