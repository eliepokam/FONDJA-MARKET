import time

from django.core.management.base import BaseCommand
from django.conf import settings

from users import messaging
from users.messaging import EnvoiImpossible, canaux_actifs, explication_erreur_twilio, envoyer_whatsapp_twilio, numeros_de_test


class Command(BaseCommand):
    help = (
        "Diagnostic de l'envoi des codes WhatsApp (Twilio) : configuration, compte, derniers messages avec leur erreur expliquée. "
        "Avec --envoyer +2376XXXXXXXX, envoie un message de test et en suit l'état."
    )

    def add_arguments(self, parser):
        parser.add_argument('--envoyer', metavar='NUMERO', help="Envoie un message de test à ce numéro (format international).")
        parser.add_argument('--messages', type=int, default=10, help="Nombre de derniers messages à afficher (défaut : 10).")
        parser.add_argument('--attente', type=int, default=4, help="Secondes d'attente avant de relire l'état du message de test.")

    def handle(self, *args, **options):
        out = self.stdout.write
        expediteur = getattr(settings, 'TWILIO_WHATSAPP_FROM', '')
        contenu = getattr(settings, 'TWILIO_WHATSAPP_CONTENT_SID', '')
        sandbox = str(expediteur).lstrip('+') == '14155238886'

        out(self.style.MIGRATE_HEADING("Configuration"))
        out(f"  Canaux actifs        : {', '.join(canaux_actifs())}" + ("" if 'sms' in canaux_actifs() else "   (SMS désactivé)"))
        out(f"  Expéditeur WhatsApp  : {expediteur}" + ("   → bac à sable Twilio" if sandbox else ""))
        out("  Mode d'envoi         : " + (f"modèle {contenu}" if contenu else "message libre (valable seulement dans les 24 h qui suivent un message du destinataire)"))
        if sandbox and not contenu:
            out(self.style.WARNING("  ! Bac à sable sans modèle : un testeur qui a rejoint il y a plus de 24 h ne recevra rien (erreur 63016). "
                                   "Renseignez TWILIO_WHATSAPP_CONTENT_SID."))
        if numeros_de_test():
            out(self.style.WARNING(f"  ! Numéros de TEST actifs ({len(numeros_de_test())}) : aucun message envoyé, code fixe. À réserver aux comptes de démonstration."))
        if sandbox:
            out("  Rappel : chaque testeur doit envoyer « join <votre code> » au numéro du bac à sable ; la connexion expire au bout de 3 jours.")

        try:
            client = messaging._client_twilio()
            compte = client.api.accounts(client.account_sid).fetch()
        except EnvoiImpossible as exc:
            out(self.style.ERROR(f"\n✗ {exc.explication}"))
            return
        except Exception as exc:   # réseau, identifiants refusés...
            out(self.style.ERROR(f"\n✗ Connexion à Twilio impossible : {exc}"))
            return
        out(self.style.SUCCESS(f"\n✓ Identifiants valides — compte « {compte.friendly_name} », type {compte.type}, statut {compte.status}"))
        if str(compte.type).lower() == 'trial':
            out(self.style.WARNING("  ! Compte d'essai : nombre de messages limité par jour, destinataires à vérifier dans la console."))

        out(self.style.MIGRATE_HEADING(f"\nDerniers messages ({options['messages']})"))
        try:
            messages = list(client.messages.list(limit=options['messages']))
        except Exception as exc:
            messages = []
            out(self.style.ERROR(f"  Lecture impossible : {exc}"))
        for m in messages:
            ligne = f"  {m.date_created:%d/%m %H:%M}  {str(m.to):<28} {str(m.status):<12}"
            out(ligne + (f" erreur {m.error_code} — {explication_erreur_twilio(m.error_code)}" if m.error_code else ""))
        if not messages:
            out("  (aucun message)")

        numero = options.get('envoyer')
        if numero:
            out(self.style.MIGRATE_HEADING(f"\nMessage de test vers {numero}"))
            try:
                sid = envoyer_whatsapp_twilio(numero, "000000")
            except EnvoiImpossible as exc:
                out(self.style.ERROR(f"✗ {exc.explication}"))
                return
            except Exception as exc:
                out(self.style.ERROR(f"✗ Échec : {exc}"))
                return
            out(f"  Envoyé, SID {sid}. Relecture de l'état dans {options['attente']} s…")
            time.sleep(options['attente'])
            etat = client.messages(sid).fetch()
            if etat.error_code:
                out(self.style.ERROR(f"  ✗ {etat.status} — erreur {etat.error_code} : {explication_erreur_twilio(etat.error_code)}"))
            else:
                out(self.style.SUCCESS(f"  ✓ état : {etat.status}"))
