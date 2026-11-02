from datetime import timedelta

from django.core.management.base import BaseCommand
from django.utils import timezone

from payments.models import Paiement
from payments.views import appliquer_statut_freemopay


class Command(BaseCommand):
    help = (
        "Resynchronise avec le prestataire les paiements restés « en attente » : rattrape un callback "
        "perdu ou un client qui a fermé la page avant la fin. À planifier toutes les 1 à 2 minutes."
    )

    def add_arguments(self, parser):
        parser.add_argument('--min-age', type=int, default=30,
                            help="Âge minimum du paiement en secondes avant de l'interroger (défaut : 30).")

    def handle(self, *args, **options):
        seuil = timezone.now() - timedelta(seconds=options['min_age'])
        en_attente = (
            Paiement.objects.filter(statut=Paiement.Statut.EN_ATTENTE, cree_le__lte=seuil)
            .exclude(reference_prestataire__isnull=True).exclude(reference_prestataire='')
            .select_related('client', 'colis')
        )
        mis_a_jour = erreurs = 0
        for paiement in en_attente:
            avant = paiement.statut
            _, code = appliquer_statut_freemopay(paiement)
            paiement.refresh_from_db()
            if code >= 500:
                erreurs += 1
            elif paiement.statut != avant:
                mis_a_jour += 1
        self.stdout.write(f"{en_attente.count()} paiement(s) vérifié(s), {mis_a_jour} mis à jour, {erreurs} erreur(s) prestataire.")
