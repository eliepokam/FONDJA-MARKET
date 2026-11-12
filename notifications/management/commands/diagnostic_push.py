import os

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from notifications.models import FCMToken
from notifications.services import firebase_pret, send_push_notification
from users.models import Utilisateur


class Command(BaseCommand):
    help = (
        "Diagnostic des notifications push (Firebase) : identifiants du serveur, projet, appareils enregistrés.\n"
        "  --envoyer +2376XXXXXXXX   envoie une notification de test à tous les appareils de ce compte et dit pourquoi elle n'arrive pas"
    )

    def add_arguments(self, parser):
        parser.add_argument('--envoyer', metavar='TELEPHONE', help="Numéro du compte à qui envoyer le test.")

    def handle(self, *args, **options):
        out = self.stdout.write
        chemin = str(getattr(settings, 'FIREBASE_CREDENTIALS_PATH', '') or '')
        out(self.style.MIGRATE_HEADING("Serveur"))
        out(f"  Fichier d'identifiants : {chemin or '(FIREBASE_CREDENTIALS_PATH non défini)'}")
        if chemin and not os.path.isabs(chemin):
            out(self.style.WARNING(f"  ! Chemin RELATIF : il dépend du dossier depuis lequel Django est lancé (ici : {os.getcwd()}). Préférez un chemin absolu."))
        if chemin and not os.path.exists(chemin):
            out(self.style.ERROR("  ✗ Fichier introuvable à cet emplacement."))
        if getattr(settings, 'DISABLE_EXTERNAL_MESSAGING', False):
            out(self.style.WARNING("  ! DISABLE_EXTERNAL_MESSAGING est actif : aucun envoi réel."))
        if not firebase_pret():
            out(self.style.ERROR("  ✗ Firebase n'est PAS initialisé : aucune notification ne peut partir. Regardez le message « Firebase non initialisé » au démarrage de Django."))
            return
        import firebase_admin
        projet = firebase_admin.get_app().project_id
        out(self.style.SUCCESS(f"  ✓ Firebase initialisé — projet du serveur : « {projet} »"))
        out("    Ce projet doit être le MÊME que celui du front (src/lib/firebase.ts : projectId, messagingSenderId, clé VAPID), sinon erreur « SenderId mismatch ».")
        base = str(getattr(settings, 'FRONT_URL', '') or '')
        out(f"  Adresse du site (FRONT_URL) : {base or '(non définie : un clic sur la notification ouvrira la page d’accueil)'}")

        out(self.style.MIGRATE_HEADING("\nAppareils enregistrés"))
        total = FCMToken.objects.count()
        out(f"  {total} appareil(s) au total")
        for jeton in FCMToken.objects.select_related('utilisateur').order_by('-id')[:10]:
            out(f"    …{jeton.token[-6:]}  →  {jeton.utilisateur.nom} ({jeton.utilisateur.telephone})")
        if total == 0:
            out(self.style.WARNING("  Aucun : le navigateur n'a jamais enregistré de jeton (permission refusée, service worker absent, ou page non servie en HTTPS)."))

        numero = options.get('envoyer')
        if numero:
            out(self.style.MIGRATE_HEADING(f"\nTest vers {numero}"))
            utilisateur = Utilisateur.objects.filter(telephone=numero).first()
            if utilisateur is None:
                raise CommandError("Aucun compte avec ce numéro.")
            resume = send_push_notification(utilisateur, "Test de notification", "Si vous lisez ce message, les notifications fonctionnent.", "/notifications")
            out(f"  Appareils : {resume['tokens']} · envoyés : {resume['envoyes']} · jetons supprimés : {resume['supprimes']}")
            for e in resume['erreurs']:
                out(self.style.ERROR(f"    appareil {e['jeton']} : {e['raison']}"))
            if resume['tokens'] == 0:
                out(self.style.WARNING("  Ce compte n'a aucun appareil enregistré : il doit d'abord activer les notifications dans son navigateur."))
            elif resume['envoyes']:
                out(self.style.SUCCESS("  ✓ Envoyé à Firebase. Si rien n'apparaît : autorisation du navigateur, mode « Ne pas déranger », ou service worker absent."))
