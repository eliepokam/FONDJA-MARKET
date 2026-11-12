"""
Débloque les paiements Mobile Money restés « en attente » (changement de prestataire, tunnel coupé, client parti avant la fin…).
Un paiement en attente empêche le client de repayer son colis : on le marque « échoué » pour qu'il puisse recommencer.

    python manage.py liberer_paiements_bloques                         # SIMULATION : affiche ce qui serait fait, ne modifie rien
    python manage.py liberer_paiements_bloques --appliquer             # libère les paiements SANS référence prestataire
    python manage.py liberer_paiements_bloques --appliquer --prestataire freemopay
                                                                       # demande d'abord le vrai statut au prestataire
    ... --forcer                                                       # libère aussi s'il est « en attente » ou injoignable
    ... --paiement 42                                                  # un seul paiement

Un paiement déjà payé n'est JAMAIS touché ; si le prestataire confirme le paiement, il est marqué payé (et le client prévenu).
"""
from django.core.management.base import BaseCommand
from django.db import transaction

from payments.models import Paiement
from payments.services import FreeMoPayClient, FreeMoPayError
from payments.views import appliquer_statut_freemopay

PRESTATAIRES = ('freemopay',)


def passerelle_pour(nom):
    return {'freemopay': FreeMoPayClient}[nom]()


class Command(BaseCommand):
    help = "Libère les paiements Mobile Money restés « en attente » pour que le client puisse repayer (simulation par défaut)."

    def add_arguments(self, parser):
        parser.add_argument('--appliquer', action='store_true', help="Applique réellement les changements (sinon : simulation).")
        parser.add_argument('--prestataire', choices=PRESTATAIRES, help="Prestataire à interroger pour les paiements qui ont une référence.")
        parser.add_argument('--forcer', action='store_true', help="Libère aussi les paiements encore en attente ou dont le prestataire ne répond pas.")
        parser.add_argument('--paiement', type=int, help="Identifiant d'un seul paiement à traiter.")

    def handle(self, *args, **options):
        appliquer, forcer = options['appliquer'], options['forcer']
        out = self.stdout.write
        if not appliquer:
            out(self.style.WARNING("SIMULATION : rien n'est modifié. Ajoutez --appliquer pour appliquer."))

        paiements = Paiement.objects.filter(statut=Paiement.Statut.EN_ATTENTE).select_related('client', 'colis')
        if options['paiement']:
            paiements = paiements.filter(pk=options['paiement'])
        passerelle = passerelle_pour(options['prestataire']) if options['prestataire'] else None

        liberes = 0
        for p in paiements:
            etiquette = f"Paiement {p.pk} (colis {p.colis.code_suivi}, {p.client.nom})"
            if not p.reference_prestataire:
                out(f"{etiquette} : aucune référence prestataire → LIBÉRÉ")
                liberes += self._liberer(p, appliquer)
                continue
            if passerelle is None:
                out(f"{etiquette} : référence {p.reference_prestataire}, statut inconnu → ignoré (relancez avec --prestataire pour l'interroger)")
                continue
            try:
                statut = (passerelle.get_status(p.reference_prestataire) or {}).get('status')
            except FreeMoPayError as exc:
                if forcer:
                    out(f"{etiquette} : prestataire injoignable ({exc}) → LIBÉRÉ (--forcer)")
                    liberes += self._liberer(p, appliquer)
                else:
                    out(f"{etiquette} : prestataire injoignable ({exc}) → laissé en attente (--forcer pour libérer)")
                continue
            if statut == 'SUCCESS':
                out(f"{etiquette} : le prestataire confirme le paiement → PAYÉ")
                if appliquer:
                    appliquer_statut_freemopay(p, gateway=passerelle)
            elif statut == 'FAILED' or forcer:
                out(f"{etiquette} : statut prestataire « {statut} » → LIBÉRÉ")
                liberes += self._liberer(p, appliquer)
            else:
                out(f"{etiquette} : encore « {statut} » chez le prestataire → laissé en attente (--forcer pour libérer)")

        out(f"{liberes} paiement(s) {'libéré(s)' if appliquer else 'à libérer'}.")

    @staticmethod
    def _liberer(paiement, appliquer):
        if appliquer:
            with transaction.atomic():
                Paiement.objects.filter(pk=paiement.pk, statut=Paiement.Statut.EN_ATTENTE).update(statut=Paiement.Statut.ECHOUE)
        return 1
