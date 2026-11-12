"""Notifications push : ne perdre aucun appareil à tort, expliquer chaque échec, tester en un clic."""
from io import StringIO
from types import SimpleNamespace
from unittest.mock import patch

from django.core.cache import cache
from django.core.management import call_command
from django.test import override_settings
from django.utils import timezone
from firebase_admin import exceptions as fb
from firebase_admin import messaging
from rest_framework.test import APITestCase, APIClient

from notifications.models import FCMToken
from notifications.services import send_push_notification
from users.models import Client


def reponse(*resultats):
    """resultats : True (succès) ou une exception Firebase (échec), un par appareil."""
    return SimpleNamespace(responses=[SimpleNamespace(success=(r is True), exception=None if r is True else r) for r in resultats])


class Base(APITestCase):
    def setUp(self):
        cache.clear()
        self.alice = Client.objects.create(nom='Alice', telephone='+237677997001', code_client='CL-PS01', telephone_verifie_le=timezone.now())
        self.jetons = [FCMToken.objects.create(utilisateur=self.alice, token=f'jeton-{i}-abcdef{i}') for i in range(3)]


class EnvoiTests(Base):
    def envoyer(self, *resultats, **kw):
        with patch('notifications.services.messaging.send_each_for_multicast', return_value=reponse(*resultats)) as appel:
            self.resume = send_push_notification(self.alice, 'Titre', 'Corps', kw.get('link', '/shipments/7'))
        self.appel = appel
        return self.resume

    def test_succes_pour_tous_les_appareils(self):
        r = self.envoyer(True, True, True)
        self.assertEqual((r['tokens'], r['envoyes'], r['supprimes'], r['erreurs']), (3, 3, 0, []))

    def test_aucun_appareil_aucun_appel(self):
        FCMToken.objects.all().delete()
        with patch('notifications.services.messaging.send_each_for_multicast') as appel:
            r = send_push_notification(self.alice, 'T', 'C')
        self.assertFalse(appel.called)
        self.assertEqual(r, {'tokens': 0, 'envoyes': 0, 'supprimes': 0, 'erreurs': []})

    def test_jeton_perime_supprime_les_autres_conserves(self):
        r = self.envoyer(True, messaging.UnregisteredError('gone'), True)
        self.assertEqual((r['envoyes'], r['supprimes']), (2, 1))
        self.assertEqual(sorted(FCMToken.objects.values_list('token', flat=True)), ['jeton-0-abcdef0', 'jeton-2-abcdef2'])
        self.assertIn('périmé', r['erreurs'][0]['raison'])
        self.assertEqual(r['erreurs'][0]['jeton'], '…bcdef1')                       # jamais le jeton entier dans les journaux

    def test_panne_passagere_ne_fait_perdre_aucun_appareil(self):
        r = self.envoyer(fb.UnavailableError('503'), messaging.QuotaExceededError('quota'), messaging.ThirdPartyAuthError('vapid'))
        self.assertEqual((r['envoyes'], r['supprimes']), (0, 0))
        self.assertEqual(FCMToken.objects.count(), 3)                                # AVANT : les 3 appareils étaient supprimés
        raisons = ' | '.join(e['raison'] for e in r['erreurs'])
        self.assertIn('indisponible', raisons); self.assertIn('quota', raisons); self.assertIn('VAPID', raisons)

    def test_projet_firebase_different_explique_et_jeton_supprime(self):
        r = self.envoyer(messaging.SenderIdMismatchError('mismatch'), True, True)
        self.assertEqual(FCMToken.objects.count(), 2)
        self.assertIn('AUTRE projet Firebase', r['erreurs'][0]['raison'])

    def test_jeton_invalide_supprime(self):
        self.envoyer(fb.InvalidArgumentError('bad'), True, True)
        self.assertEqual(FCMToken.objects.count(), 2)

    def test_echec_journalise_en_avertissement_sans_jeton_complet(self):
        with self.assertLogs('django', level='WARNING') as journal:
            self.envoyer(messaging.UnregisteredError('gone'), True, True)
        sortie = ' '.join(journal.output)
        self.assertIn('périmé', sortie)
        self.assertNotIn('jeton-1-abcdef1', sortie)

    @override_settings(FRONT_URL='https://mon-domaine.ngrok-free.dev/')
    def test_le_clic_ouvre_la_bonne_page_grace_a_l_adresse_absolue(self):
        self.envoyer(True, True, True, link='/shipments/7')
        message = self.appel.call_args.args[0]
        self.assertEqual(message.webpush.fcm_options.link, 'https://mon-domaine.ngrok-free.dev/shipments/7')
        self.assertEqual(message.webpush.notification.icon, 'https://mon-domaine.ngrok-free.dev/icons/icon-192.png')
        self.assertEqual(message.webpush.notification.badge, 'https://mon-domaine.ngrok-free.dev/icons/badge-96.png')
        self.assertEqual(message.webpush.headers, {'TTL': '86400', 'Urgency': 'high'})
        self.assertEqual(message.data, {'link': '/shipments/7'})

    def test_sans_adresse_du_site_pas_d_icone_mais_la_duree_de_vie_reste(self):
        self.envoyer(True, True, True)
        webpush = self.appel.call_args.args[0].webpush
        self.assertEqual(webpush.headers, {'TTL': '86400', 'Urgency': 'high'})
        self.assertIsNone(webpush.notification)
        self.assertIsNone(webpush.fcm_options)


class TestEnUnClicTests(Base):
    URL = '/api/notifications/test-push/'

    def appeler(self, user=None):
        api = APIClient(); api.force_authenticate(user=user or self.alice)
        return api.post(self.URL)

    def test_firebase_non_configure_message_clair(self):
        with patch('notifications.services.firebase_pret', return_value=False):
            r = self.appeler()
        self.assertEqual(r.status_code, 503)
        self.assertIn('FIREBASE_CREDENTIALS_PATH', r.json()['detail'])

    def test_aucun_appareil_dit_quoi_faire(self):
        FCMToken.objects.all().delete()
        with patch('notifications.services.firebase_pret', return_value=True):
            r = self.appeler()
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json()['tokens'], 0)
        self.assertIn('Activez les notifications', r.json()['detail'])

    def test_succes(self):
        with patch('notifications.services.firebase_pret', return_value=True), \
             patch('notifications.services.messaging.send_each_for_multicast', return_value=reponse(True, True, True)):
            r = self.appeler()
        self.assertEqual(r.json()['envoyes'], 3)
        self.assertIn('3 appareil(s)', r.json()['detail'])

    def test_refus_de_firebase_explique(self):
        with patch('notifications.services.firebase_pret', return_value=True), \
             patch('notifications.services.messaging.send_each_for_multicast', return_value=reponse(*[messaging.SenderIdMismatchError('x')] * 3)):
            r = self.appeler()
        self.assertIn('AUTRE projet', r.json()['detail'])

    def test_il_faut_etre_connecte(self):
        self.assertEqual(APIClient().post(self.URL).status_code, 401)


class DiagnosticTests(Base):
    def lancer(self, *args):
        sortie = StringIO()
        call_command('diagnostic_push', *args, stdout=sortie)
        return sortie.getvalue()

    def test_firebase_absent(self):
        with patch('notifications.management.commands.diagnostic_push.firebase_pret', return_value=False):
            self.assertIn("n'est PAS initialisé", self.lancer())

    @override_settings(FIREBASE_CREDENTIALS_PATH='cle.json')
    def test_chemin_relatif_signale(self):
        with patch('notifications.management.commands.diagnostic_push.firebase_pret', return_value=False):
            self.assertIn('RELATIF', self.lancer())

    def test_envoi_de_test_et_appareils(self):
        app = SimpleNamespace(project_id='fondja-market')
        with patch('notifications.management.commands.diagnostic_push.firebase_pret', return_value=True), \
             patch('firebase_admin.get_app', return_value=app), \
             patch('notifications.services.messaging.send_each_for_multicast', return_value=reponse(True, messaging.UnregisteredError('x'), True)):
            sortie = self.lancer('--envoyer=+237677997001')
        self.assertIn('fondja-market', sortie)
        self.assertIn('3 appareil(s) au total', sortie)
        self.assertIn('envoyés : 2', sortie)
        self.assertIn('périmé', sortie)
