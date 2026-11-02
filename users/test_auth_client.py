"""Parcours d'authentification du CLIENT : inscription, vérification du numéro, connexion, session, mots de passe."""
from datetime import timedelta
from unittest.mock import patch

from django.core.cache import cache
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APITestCase, APIClient

from users.models import Agent, Administrateur, Client, CodeOtp

MOT_DE_PASSE = 'Zebre!Orange2026'


class SansLimiteDeDebit(APITestCase):
    """Le throttle anonyme (20 requêtes/heure/IP) est conservé en vrai ; on le remet à zéro entre deux tests."""

    def setUp(self):
        super().setUp()
        cache.clear()


def client_verifie(tel='+237677100001', code='CL-AU01', verifie=True):
    c = Client.objects.create(nom='Jean Test', telephone=tel, code_client=code,
                              telephone_verifie_le=timezone.now() if verifie else None)
    c.set_password(MOT_DE_PASSE)
    c.save()
    return c


def connecter(api, tel, mot_de_passe=MOT_DE_PASSE, portail='client'):
    return api.post('/api/auth/login/', {'telephone': tel, 'password': mot_de_passe, 'portail': portail}, format='json')


class InscriptionTests(SansLimiteDeDebit):
    def inscrire(self, **extra):
        data = {'nom': 'Awa Test', 'telephone': '+237677100010', 'password': MOT_DE_PASSE}
        data.update(extra)
        return self.client.post('/api/auth/register/', data, format='json')

    def test_inscription_valide_attribue_un_code_client(self):
        r = self.inscrire()
        self.assertEqual(r.status_code, 201, r.data)
        c = Client.objects.get(telephone='+237677100010')
        self.assertEqual(c.code_client, f'CL-{c.pk:05d}')
        self.assertIsNone(c.telephone_verifie_le)

    def test_mot_de_passe_trop_court_refuse(self):
        r = self.inscrire(password='abc12')
        self.assertEqual(r.status_code, 400)
        self.assertIn('password', r.data)

    def test_mot_de_passe_trivial_refuse(self):
        for faible in ('12345678', 'password', 'azertyuiop'):
            r = self.inscrire(password=faible)
            self.assertEqual(r.status_code, 400, faible)
            self.assertIn('password', r.data)

    def test_numero_deja_utilise_renvoie_un_message_en_francais(self):
        client_verifie('+237677100010')
        r = self.inscrire()
        self.assertEqual(r.status_code, 400)
        self.assertIn('telephone', r.data)
        self.assertNotIn('already exists', str(r.data['telephone']))


class ConnexionEtVerificationTests(SansLimiteDeDebit):
    def test_un_client_non_verifie_ne_peut_pas_se_connecter(self):
        client_verifie('+237677100020', 'CL-AU20', verifie=False)
        r = connecter(self.client, '+237677100020')
        self.assertEqual(r.status_code, 403)
        self.assertEqual(r.data['code'], 'phone_not_verified')
        self.assertNotIn('access_token', r.cookies)               # aucune session ouverte

    def test_un_client_verifie_se_connecte_et_recoit_des_cookies_durables(self):
        client_verifie('+237677100021', 'CL-AU21')
        r = connecter(self.client, '+237677100021')
        self.assertEqual(r.status_code, 200, r.data)
        self.assertGreater(int(r.cookies['access_token']['max-age']), 0)    # survit à la fermeture du navigateur
        self.assertGreater(int(r.cookies['refresh_token']['max-age']), int(r.cookies['access_token']['max-age']))

    def test_le_personnel_non_verifie_n_est_pas_bloque_sur_son_portail(self):
        admin = Administrateur.objects.create(nom='Admin', telephone='+237677100022'); admin.set_password(MOT_DE_PASSE); admin.save()
        agent = Agent.objects.create(nom='Agent', telephone='+237677100023', localisation='chine', cree_par=admin)
        agent.set_password(MOT_DE_PASSE); agent.save()
        self.assertEqual(connecter(self.client, '+237677100023', portail='staff').status_code, 200)

    def test_inscription_puis_verification_puis_connexion(self):
        self.client.post('/api/auth/register/', {'nom': 'Awa', 'telephone': '+237677100024', 'password': MOT_DE_PASSE}, format='json')
        otp = CodeOtp.objects.get(utilisateur__telephone='+237677100024')
        self.assertEqual(connecter(self.client, '+237677100024').status_code, 403)
        r = self.client.post('/api/auth/otp/verify/', {'telephone': '+237677100024', 'code': otp.code}, format='json')
        self.assertEqual(r.status_code, 200)
        self.assertEqual(connecter(APIClient(), '+237677100024').status_code, 200)

    def test_la_verification_otp_est_refusee_pour_un_compte_deja_verifie(self):
        c = client_verifie('+237677100025', 'CL-AU25')
        CodeOtp.objects.create(utilisateur=c, code='123456', expire_le=timezone.now() + timedelta(minutes=5))
        r = self.client.post('/api/auth/otp/verify/', {'telephone': '+237677100025', 'code': '123456'}, format='json')
        self.assertEqual(r.status_code, 400)
        self.assertNotIn('access_token', r.cookies)               # un code SMS ne remplace JAMAIS le mot de passe

    def test_la_verification_otp_est_refusee_pour_le_personnel(self):
        admin = Administrateur.objects.create(nom='Admin', telephone='+237677100026')
        CodeOtp.objects.create(utilisateur=admin, code='123456', expire_le=timezone.now() + timedelta(minutes=5))
        r = self.client.post('/api/auth/otp/verify/', {'telephone': '+237677100026', 'code': '123456'}, format='json')
        self.assertEqual(r.status_code, 400)
        self.assertNotIn('access_token', r.cookies)

    def test_la_reinitialisation_du_mot_de_passe_confirme_le_numero_et_leve_le_blocage(self):
        c = client_verifie('+237677100027', 'CL-AU27', verifie=False)
        c.tentatives_connexion_echouees = 5
        c.bloque_jusqu_a = timezone.now() + timedelta(minutes=15)
        c.save()
        CodeOtp.objects.create(utilisateur=c, code='654321', expire_le=timezone.now() + timedelta(minutes=5))
        r = self.client.post('/api/auth/reset-password/', {'telephone': '+237677100027', 'code': '654321',
                                                           'new_password': 'Nouveau!Mdp2026'}, format='json')
        self.assertEqual(r.status_code, 200, r.data)
        c.refresh_from_db()
        self.assertIsNotNone(c.telephone_verifie_le)
        self.assertIsNone(c.bloque_jusqu_a)
        self.assertEqual(c.tentatives_connexion_echouees, 0)
        self.assertEqual(connecter(APIClient(), '+237677100027', 'Nouveau!Mdp2026').status_code, 200)

    def test_reinitialisation_avec_mot_de_passe_trivial_refusee(self):
        c = client_verifie('+237677100028', 'CL-AU28')
        CodeOtp.objects.create(utilisateur=c, code='654321', expire_le=timezone.now() + timedelta(minutes=5))
        r = self.client.post('/api/auth/reset-password/', {'telephone': '+237677100028', 'code': '654321',
                                                           'new_password': '12345678'}, format='json')
        self.assertEqual(r.status_code, 400)


class RenvoiOtpTests(SansLimiteDeDebit):
    def renvoyer(self, tel):
        return self.client.post('/api/auth/otp/resend/', {'telephone': tel, 'canal': 'sms'}, format='json')

    def test_numero_inconnu_ne_revele_rien(self):
        r = self.renvoyer('+237677100030')
        self.assertEqual(r.status_code, 200)

    def test_compte_deja_verifie_ne_recoit_aucun_code(self):
        c = client_verifie('+237677100031', 'CL-AU31')
        self.assertEqual(self.renvoyer('+237677100031').status_code, 200)
        self.assertFalse(CodeOtp.objects.filter(utilisateur=c).exists())

    def test_client_non_verifie_recoit_un_code_puis_le_delai_s_applique(self):
        c = client_verifie('+237677100032', 'CL-AU32', verifie=False)
        self.assertEqual(self.renvoyer('+237677100032').status_code, 200)
        self.assertEqual(CodeOtp.objects.filter(utilisateur=c).count(), 1)
        self.assertEqual(self.renvoyer('+237677100032').status_code, 429)        # moins de 60 s après le précédent

    def test_plafond_horaire_de_codes(self):
        c = client_verifie('+237677100033', 'CL-AU33', verifie=False)
        for _ in range(5):
            CodeOtp.objects.create(utilisateur=c, code='111111', expire_le=timezone.now() + timedelta(minutes=5))
        CodeOtp.objects.filter(utilisateur=c).update(cree_le=timezone.now() - timedelta(minutes=5))
        r = self.renvoyer('+237677100033')
        self.assertEqual(r.status_code, 429)
        self.assertEqual(CodeOtp.objects.filter(utilisateur=c).count(), 5)          # aucun SMS supplémentaire


class SessionTests(SansLimiteDeDebit):
    def test_rafraichir_sans_session_renvoie_401_pas_500(self):
        r = self.client.post('/api/auth/login/refresh/')
        self.assertEqual(r.status_code, 401)

    def test_rafraichir_avec_un_jeton_revoque_renvoie_401_et_nettoie_les_cookies(self):
        client_verifie('+237677100040', 'CL-AU40')
        api = APIClient()
        connecter(api, '+237677100040')
        api.post('/api/auth/logout/')                                  # révoque le jeton de rafraîchissement
        api.cookies['refresh_token'] = api.cookies['refresh_token'].value if 'refresh_token' in api.cookies else 'invalide'
        r = api.post('/api/auth/login/refresh/')
        self.assertEqual(r.status_code, 401)

    def test_rafraichir_avec_une_session_valide(self):
        client_verifie('+237677100041', 'CL-AU41')
        api = APIClient()
        connecter(api, '+237677100041')
        r = api.post('/api/auth/login/refresh/')
        self.assertEqual(r.status_code, 200)
        self.assertIn('access_token', r.cookies)

    def test_changer_de_mot_de_passe_garde_cet_appareil_connecte_et_deconnecte_les_autres(self):
        client_verifie('+237677100042', 'CL-AU42')
        appareil_1, appareil_2 = APIClient(), APIClient()
        connecter(appareil_1, '+237677100042')
        connecter(appareil_2, '+237677100042')

        r = appareil_1.post('/api/auth/change-password/', {'current_password': MOT_DE_PASSE,
                                                           'new_password': 'Tout!Neuf2026x'}, format='json')
        self.assertEqual(r.status_code, 200, r.data)
        self.assertIn('refresh_token', r.cookies)                      # session neuve pour l'appareil courant
        self.assertEqual(appareil_1.post('/api/auth/login/refresh/').status_code, 200)
        self.assertEqual(appareil_2.post('/api/auth/login/refresh/').status_code, 401)

    def test_changement_de_mot_de_passe_refuse_si_trivial_ou_si_ancien_incorrect(self):
        client_verifie('+237677100043', 'CL-AU43')
        api = APIClient()
        connecter(api, '+237677100043')
        r = api.post('/api/auth/change-password/', {'current_password': MOT_DE_PASSE, 'new_password': '12345678'}, format='json')
        self.assertEqual(r.status_code, 400)
        self.assertIn('new_password', r.data)
        r = api.post('/api/auth/change-password/', {'current_password': 'mauvais', 'new_password': 'Tout!Neuf2026x'}, format='json')
        self.assertEqual(r.status_code, 400)
        self.assertIn('current_password', r.data)
