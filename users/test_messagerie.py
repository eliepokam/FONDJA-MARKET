"""Canaux disponibles, envoi WhatsApp via Twilio (message libre / modèle), erreurs expliquées, diagnostic."""
import json
from io import StringIO
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from django.core.cache import cache
from django.core.management import call_command
from django.test import SimpleTestCase, override_settings
from django.utils import timezone
from rest_framework.test import APITestCase, APIClient
from twilio.base.exceptions import TwilioRestException

from users import messaging
from users.messaging import EnvoiImpossible, aide_bac_a_sable, canaux_actifs, choisir_canal, envoyer_whatsapp_twilio
from users.models import Client, CodeOtp
from users.tasks import envoyer_otp_task

MDP = 'Zebre!Orange2026'


class CanauxTests(SimpleTestCase):
    def test_par_defaut_les_deux_canaux(self):
        self.assertEqual(canaux_actifs(), ['whatsapp', 'sms'])

    @override_settings(OTP_CANAUX=['whatsapp'])
    def test_sms_desactive(self):
        self.assertEqual(canaux_actifs(), ['whatsapp'])
        self.assertEqual(choisir_canal('sms'), 'whatsapp')                # SMS demandé : on bascule sur ce qui existe
        self.assertEqual(choisir_canal('whatsapp'), 'whatsapp')
        self.assertEqual(choisir_canal(None), 'whatsapp')
        self.assertEqual(choisir_canal('telegram'), 'whatsapp')

    @override_settings(OTP_CANAUX='whatsapp, sms')
    def test_valeur_texte_separee_par_des_virgules(self):
        self.assertEqual(canaux_actifs(), ['whatsapp', 'sms'])

    @override_settings(OTP_CANAUX=['fax', ''])
    def test_valeurs_inconnues_ne_laissent_jamais_zero_canal(self):
        self.assertEqual(canaux_actifs(), ['whatsapp'])

    @override_settings(OTP_CANAUX=['sms', 'whatsapp'])
    def test_sms_prefere_si_c_est_l_ordre_choisi(self):
        self.assertEqual(choisir_canal(None), 'sms')


@override_settings(OTP_CANAUX=['whatsapp'])
class SmsIndisponibleDansLesParcoursTests(APITestCase):
    """Sans SMS, toutes les demandes de code partent par WhatsApp, même si le client demande « sms »."""

    def setUp(self):
        cache.clear()
        self.client_user = Client.objects.create(nom='Awa', telephone='+237677870001', code_client='CL-MS01',
                                                 telephone_verifie_le=None)
        self.client_user.set_password(MDP); self.client_user.save()

    def test_inscription_resend_oubli_et_suppression(self):
        with patch('users.views.lancer_tache') as envoi:
            r = APIClient().post('/api/auth/register/', {'nom': 'Neuf', 'telephone': '+237677870002', 'password': MDP,
                                                          'accepte_conditions': True, 'canal': 'sms'}, format='json')
            self.assertEqual(r.status_code, 201, r.data)
            self.assertEqual(envoi.call_args.args[3], 'whatsapp')
            self.assertEqual(CodeOtp.objects.get(utilisateur__telephone='+237677870002').canal, 'whatsapp')

            APIClient().post('/api/auth/otp/resend/', {'telephone': '+237677870001', 'canal': 'sms'}, format='json')
            self.assertEqual(envoi.call_args.args[3], 'whatsapp')

            self.client_user.telephone_verifie_le = timezone.now(); self.client_user.save()
            cache.clear(); CodeOtp.objects.all().delete()          # remet à zéro le délai de 60 s entre deux codes
            APIClient().post('/api/auth/forgot-password/', {'telephone': '+237677870001', 'canal': 'sms'}, format='json')
            self.assertEqual(envoi.call_args.args[3], 'whatsapp')

            CodeOtp.objects.all().delete()
            APIClient().post('/api/auth/delete-account/request-code/', {'telephone': '+237677870001', 'canal': 'sms'}, format='json')
            self.assertEqual(envoi.call_args.args[3], 'whatsapp')
            self.assertEqual(envoi.call_count, 4)


class ConfigurationPubliqueTests(APITestCase):
    def lire(self):
        return APIClient().get('/api/auth/otp-config/')

    def test_lisible_sans_compte_et_mise_en_cache(self):
        r = self.lire()
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json(), {'canaux': ['whatsapp', 'sms'], 'whatsapp_sandbox': None})
        self.assertIn('max-age=60', r['Cache-Control'])

    @override_settings(OTP_CANAUX=['whatsapp'])
    def test_sans_sms(self):
        self.assertEqual(self.lire().json()['canaux'], ['whatsapp'])

    @override_settings(OTP_CANAUX=['whatsapp'], TWILIO_SANDBOX_JOIN_CODE='join bright-tiger', TWILIO_WHATSAPP_FROM='+14155238886')
    def test_aide_du_bac_a_sable_en_phase_de_test(self):
        self.assertEqual(self.lire().json()['whatsapp_sandbox'], {'numero': '+14155238886', 'message': 'join bright-tiger'})

    @override_settings(OTP_CANAUX=['sms'], TWILIO_SANDBOX_JOIN_CODE='join bright-tiger')
    def test_pas_d_aide_whatsapp_si_whatsapp_n_est_pas_un_canal(self):
        self.assertIsNone(aide_bac_a_sable())


def faux_client(sid='SM123'):
    client = MagicMock()
    client.messages.create.return_value = SimpleNamespace(sid=sid)
    return client


class EnvoiWhatsappTests(SimpleTestCase):
    @override_settings(TWILIO_WHATSAPP_FROM='+14155238886', TWILIO_WHATSAPP_CONTENT_SID='')
    def test_message_libre(self):
        client = faux_client()
        with patch('users.messaging._client_twilio', return_value=client):
            sid = envoyer_whatsapp_twilio('+237677123456', '482913')
        self.assertEqual(sid, 'SM123')
        kw = client.messages.create.call_args.kwargs
        self.assertEqual((kw['from_'], kw['to']), ('whatsapp:+14155238886', 'whatsapp:+237677123456'))
        self.assertIn('482913', kw['body'])
        self.assertNotIn('content_sid', kw)

    @override_settings(TWILIO_WHATSAPP_CONTENT_SID='HXabc', TWILIO_WHATSAPP_CONTENT_VARIABLES='')
    def test_modele_a_une_variable_par_defaut(self):
        client = faux_client()
        with patch('users.messaging._client_twilio', return_value=client):
            envoyer_whatsapp_twilio('+237677123456', '482913')
        kw = client.messages.create.call_args.kwargs
        self.assertEqual(kw['content_sid'], 'HXabc')
        self.assertEqual(json.loads(kw['content_variables']), {'1': '482913'})
        self.assertNotIn('body', kw)

    @override_settings(TWILIO_WHATSAPP_CONTENT_SID='HXsandbox', TWILIO_WHATSAPP_CONTENT_VARIABLES='{"1":"{service}","2":"{code}"}')
    def test_modele_verification_du_bac_a_sable_a_deux_variables(self):
        client = faux_client()
        with patch('users.messaging._client_twilio', return_value=client):
            envoyer_whatsapp_twilio('+237677123456', '111222')
        self.assertEqual(json.loads(client.messages.create.call_args.kwargs['content_variables']), {'1': 'Fondja Market', '2': '111222'})

    @override_settings(TWILIO_WHATSAPP_CONTENT_SID='HXabc', TWILIO_WHATSAPP_CONTENT_VARIABLES='pas du json')
    def test_variables_invalides_expliquees(self):
        with patch('users.messaging._client_twilio', return_value=faux_client()):
            with self.assertRaisesMessage(EnvoiImpossible, 'JSON'):
                envoyer_whatsapp_twilio('+237677123456', '1')

    @override_settings(TWILIO_ACCOUNT_SID='', TWILIO_AUTH_TOKEN='')
    def test_identifiants_absents_expliques(self):
        with self.assertRaisesMessage(EnvoiImpossible, 'TWILIO_ACCOUNT_SID'):
            envoyer_whatsapp_twilio('+237677123456', '1')

    def erreur(self, code, statut=400):
        client = MagicMock()
        client.messages.create.side_effect = TwilioRestException(statut, 'https://api.twilio.com', 'boom', code)
        return client

    def test_destinataire_pas_dans_le_bac_a_sable(self):
        with patch('users.messaging._client_twilio', return_value=self.erreur(63015)):
            with self.assertRaises(EnvoiImpossible) as ctx:
                envoyer_whatsapp_twilio('+237677123456', '1')
        self.assertEqual(ctx.exception.code, 63015)
        self.assertIn('join', ctx.exception.explication)

    def test_hors_fenetre_de_24h_conseille_le_modele(self):
        with patch('users.messaging._client_twilio', return_value=self.erreur(63016)):
            with self.assertRaises(EnvoiImpossible) as ctx:
                envoyer_whatsapp_twilio('+237677123456', '1')
        self.assertIn('TWILIO_WHATSAPP_CONTENT_SID', ctx.exception.explication)

    def test_les_pannes_passageres_ne_sont_pas_definitives(self):
        for code, statut in ((20429, 429), (30008, 500), (None, 503)):
            with patch('users.messaging._client_twilio', return_value=self.erreur(code, statut)):
                with self.assertRaises(TwilioRestException):
                    envoyer_whatsapp_twilio('+237677123456', '1')


@override_settings(DISABLE_EXTERNAL_MESSAGING=False)
class TacheEnvoiTests(SimpleTestCase):
    def test_echec_definitif_pas_de_nouvelle_tentative_et_explication_dans_les_journaux(self):
        with patch('users.tasks._envoyer_whatsapp_twilio', side_effect=EnvoiImpossible('Le destinataire n\'a PAS rejoint le bac à sable', 63015)) as envoi:
            with self.assertLogs('django', level='ERROR') as journal:
                resultat = envoyer_otp_task.apply(args=('+237677123456', '482913', 'whatsapp'))
        self.assertTrue(resultat.successful())
        self.assertEqual(envoi.call_count, 1)                                         # une seule tentative : réessayer ne changerait rien
        self.assertIn('bac à sable', ' '.join(journal.output))

    def test_panne_passagere_reessayee_puis_abandonnee(self):
        with patch('users.tasks._envoyer_whatsapp_twilio', side_effect=ConnectionError('réseau')) as envoi:
            with self.assertLogs('django', level='ERROR'):
                resultat = envoyer_otp_task.apply(args=('+237677123456', '482913', 'whatsapp'))
        self.assertTrue(resultat.failed())
        self.assertEqual(envoi.call_count, 4)                                         # 1 essai + 3 nouvelles tentatives

    def test_succes_journalise_le_sid_twilio(self):
        with patch('users.tasks._envoyer_whatsapp_twilio', return_value='SM42'):
            with self.assertLogs('django', level='INFO') as journal:
                envoyer_otp_task.apply(args=('+237677123456', '482913', 'whatsapp'))
        self.assertIn('SM42', ' '.join(journal.output))

    def test_le_code_est_transmis_a_twilio_sans_texte_de_message(self):
        with patch('users.tasks._envoyer_whatsapp_twilio', return_value='SM1') as envoi:
            envoyer_otp_task.apply(args=('+237677123456', '482913', 'whatsapp'))
        self.assertEqual(envoi.call_args.args, ('+237677123456', '482913'))


class DiagnosticTests(SimpleTestCase):
    def client_diag(self, messages=(), compte_type='Trial'):
        client = MagicMock()
        client.account_sid = 'ACxxx'
        client.api.accounts.return_value.fetch.return_value = SimpleNamespace(friendly_name='Fondja', type=compte_type, status='active')
        client.messages.list.return_value = list(messages)
        client.messages.return_value.fetch.return_value = SimpleNamespace(status='delivered', error_code=None)
        client.messages.create.return_value = SimpleNamespace(sid='SM9')
        return client

    def lancer(self, client, *args):
        sortie = StringIO()
        with patch('users.messaging._client_twilio', return_value=client):
            call_command('diagnostic_twilio', *args, '--attente=0', stdout=sortie)
        return sortie.getvalue()

    @override_settings(OTP_CANAUX=['whatsapp'], TWILIO_WHATSAPP_FROM='+14155238886', TWILIO_WHATSAPP_CONTENT_SID='')
    def test_configuration_et_avertissement_bac_a_sable_sans_modele(self):
        sortie = self.lancer(self.client_diag())
        self.assertIn('SMS désactivé', sortie)
        self.assertIn('bac à sable Twilio', sortie)
        self.assertIn('63016', sortie)                                              # avertissement : message libre = 24 h seulement
        self.assertIn('Identifiants valides', sortie)
        self.assertIn('Compte d\'essai', sortie)

    def test_derniers_messages_avec_erreur_expliquee(self):
        m = SimpleNamespace(date_created=timezone.now(), to='whatsapp:+237677000000', status='undelivered', error_code=63015, sid='SM1')
        sortie = self.lancer(self.client_diag([m]))
        self.assertIn('undelivered', sortie)
        self.assertIn('63015', sortie)
        self.assertIn('rejoint le bac à sable', sortie)

    @override_settings(TWILIO_WHATSAPP_CONTENT_SID='HXabc')
    def test_envoi_de_test_et_suivi_de_l_etat(self):
        client = self.client_diag()
        sortie = self.lancer(client, '--envoyer=+237677123456')
        self.assertIn('SM9', sortie)
        self.assertIn('delivered', sortie)
        self.assertIn('modèle HXabc', sortie)

    def test_envoi_de_test_en_echec_affiche_la_cause(self):
        client = self.client_diag()
        client.messages.return_value.fetch.return_value = SimpleNamespace(status='undelivered', error_code=63015)
        sortie = self.lancer(client, '--envoyer=+237677123456')
        self.assertIn('63015', sortie)

    @override_settings(TWILIO_ACCOUNT_SID='', TWILIO_AUTH_TOKEN='')
    def test_identifiants_absents_message_clair_sans_plantage(self):
        sortie = StringIO()
        call_command('diagnostic_twilio', stdout=sortie)
        self.assertIn('TWILIO_ACCOUNT_SID', sortie.getvalue())

    def test_connexion_impossible_message_clair_sans_plantage(self):
        client = self.client_diag()
        client.api.accounts.return_value.fetch.side_effect = TwilioRestException(401, 'u', 'Authenticate', 20003)
        sortie = self.lancer(client)
        self.assertIn('Connexion à Twilio impossible', sortie)


class LimiteQuotidienneTests(SimpleTestCase):
    def test_plafond_quotidien_est_definitif_et_explique(self):
        client = MagicMock()
        client.messages.create.side_effect = TwilioRestException(429, 'u', 'exceeded the 5 daily messages limit', 63038)
        with patch('users.messaging._client_twilio', return_value=client):
            with self.assertRaises(EnvoiImpossible) as ctx:
                envoyer_whatsapp_twilio('+237677123456', '1')
        self.assertEqual(ctx.exception.code, 63038)
        self.assertIn('QUOTIDIEN', ctx.exception.explication)
        self.assertIn('OTP_NUMEROS_DE_TEST', ctx.exception.explication)

    @override_settings(DISABLE_EXTERNAL_MESSAGING=False)
    def test_la_tache_n_insiste_pas_quand_le_plafond_est_atteint(self):
        client = MagicMock()
        client.messages.create.side_effect = TwilioRestException(429, 'u', 'limit', 63038)
        with patch('users.messaging._client_twilio', return_value=client):
            with self.assertLogs('django', level='ERROR') as journal:
                resultat = envoyer_otp_task.apply(args=('+237677123456', '482913', 'whatsapp'))
        self.assertTrue(resultat.successful())
        self.assertEqual(client.messages.create.call_count, 1)                         # avant : 4 appels (1 + 3 reprises) pour rien
        self.assertIn('QUOTIDIEN', ' '.join(journal.output))


@override_settings(OTP_NUMEROS_DE_TEST=['+237 656 64 56 49', '237670000000'], OTP_CODE_DE_TEST='123456', DISABLE_EXTERNAL_MESSAGING=False)
class NumerosDeTestTests(APITestCase):
    def setUp(self):
        cache.clear()

    def test_code_fixe_et_aucun_envoi_pour_un_numero_de_test(self):
        with patch('users.tasks._envoyer_whatsapp_twilio') as twilio:
            r = APIClient().post('/api/auth/register/', {'nom': 'Demo', 'telephone': '+237656645649', 'password': MDP,
                                                          'accepte_conditions': True, 'canal': 'whatsapp'}, format='json')
            self.assertEqual(r.status_code, 201, r.data)
            self.assertEqual(CodeOtp.objects.get(utilisateur__telephone='+237656645649').code, '123456')
            self.assertFalse(twilio.called)                                           # aucun message consommé

    def test_le_code_de_test_permet_de_valider_le_compte(self):
        APIClient().post('/api/auth/register/', {'nom': 'Demo', 'telephone': '+237656645649', 'password': MDP,
                                                  'accepte_conditions': True}, format='json')
        r = APIClient().post('/api/auth/otp/verify/', {'telephone': '+237656645649', 'code': '123456'}, format='json')
        self.assertEqual(r.status_code, 200, r.data)
        self.assertIsNotNone(Client.objects.get(telephone='+237656645649').telephone_verifie_le)

    def test_un_numero_ordinaire_garde_un_code_aleatoire_et_un_vrai_envoi(self):
        with patch('users.tasks._envoyer_whatsapp_twilio', return_value='SM1') as twilio:
            APIClient().post('/api/auth/register/', {'nom': 'Vrai', 'telephone': '+237677999111', 'password': MDP,
                                                      'accepte_conditions': True, 'canal': 'whatsapp'}, format='json')
            code = CodeOtp.objects.get(utilisateur__telephone='+237677999111').code
        self.assertRegex(code, r'^\d{6}$')
        self.assertTrue(twilio.called)

    @override_settings(OTP_CODE_DE_TEST='')
    def test_sans_code_de_test_defini_le_mode_est_inactif_meme_avec_une_liste(self):
        from users.messaging import numeros_de_test
        self.assertEqual(numeros_de_test(), [])

    @override_settings(OTP_NUMEROS_DE_TEST=[])
    def test_sans_liste_le_mode_est_inactif(self):
        from users.messaging import numeros_de_test, generer_code
        self.assertEqual(numeros_de_test(), [])
        self.assertNotEqual(generer_code('+237656645649') + 'x', '123456x')
