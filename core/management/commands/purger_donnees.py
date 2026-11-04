from django.core.management.base import BaseCommand

from core.retention import purger_donnees, reglages

LIBELLES = {
    'codes_otp': "Codes de vérification expirés",
    'jetons_expires': "Jetons de session expirés",
    'notifications': "Notifications anciennes",
    'comptes_non_verifies': "Comptes jamais vérifiés (sans colis ni paiement)",
    'pieces_comptables_comptes': "Comptes supprimés dont la durée de conservation est écoulée",
    'pieces_comptables_fichiers': "  dont fichiers (photos, vidéos) effacés du disque",
}


class Command(BaseCommand):
    help = "Purge les données arrivées au terme de leur durée de conservation. Utiliser --dry-run pour simuler sans rien supprimer."

    def add_arguments(self, parser):
        parser.add_argument('--dry-run', action='store_true', help="Affiche ce qui serait supprimé, sans rien supprimer.")

    def handle(self, *args, **options):
        dry = options['dry_run']
        r = reglages()
        self.stdout.write(self.style.WARNING("SIMULATION : rien n'est supprimé") if dry else self.style.SUCCESS("PURGE RÉELLE"))
        self.stdout.write(
            f"Durées : codes {r['OTP_JOURS']} j · notifications {r['NOTIFICATIONS_JOURS']} j · comptes non vérifiés "
            f"{r['COMPTES_NON_VERIFIES_JOURS']} j · pièces comptables "
            + (f"{r['PIECES_COMPTABLES_ANNEES']} an(s) après suppression du compte" if r['PIECES_COMPTABLES_ANNEES'] else "DÉSACTIVÉES")
        )
        for cle, nombre in purger_donnees(dry_run=dry).items():
            self.stdout.write(f"  {LIBELLES[cle]:<62} {nombre}")
