"""Consentement aux conditions d'utilisation et à la politique de confidentialité."""
from django.core.cache import cache
from django.test import override_settings
from django.utils import timezone
from rest_framework.test import APITestCase, APIClient

from users.account_deletion import anonymiser_client
from users.models import Administrateur, Client

MDP = 'Zebre!Orange2026'


def donnees(**extra):
    base = {'nom': 'Awa Test', 'telephone': '+237677830001', 'password': MDP}
    base.update(extra)
    return base


class InscriptionAvecConsentementTests(APITestCase):
    def setUp(self):
        cache.clear()

    def inscrire(self, data):
        return APIClient().post('/api/auth/register/', data, format='json')

    def test_sans_la_case_cochee_pas_de_compte(self):
        r = self.inscrire(donnees())
        self.assertEqual(r.status_code, 400)
        self.assertIn('accepte_conditions', r.data)
        self.assertIn("conditions d'utilisation", str(r.data['accepte_conditions'][0]))
        self.assertFalse(Client.objects.exists())

    def test_refus_explicite_pas_de_compte(self):
        for valeur in (False, 'false', 0, None):
            r = self.inscrire(donnees(accepte_conditions=valeur))
            self.assertEqual(r.status_code, 400, valeur)
        self.assertFalse(Client.objects.exists())

    def test_avec_consentement_la_preuve_est_enregistree(self):
        avant = timezone.now()
        r = self.inscrire(donnees(accepte_conditions=True))
        self.assertEqual(r.status_code, 201, r.data)
        c = Client.objects.get(telephone='+237677830001')
        self.assertGreaterEqual(c.consentement_le, avant)
        self.assertEqual(c.consentement_version, '2026-11-03')
        self.assertTrue(c.consentement_a_jour)
        self.assertNotIn('accepte_conditions', r.data)                      # champ d'entrée uniquement

    @override_settings(CONSENT_VERSION='2027-01-15')
    def test_la_version_enregistree_suit_le_reglage(self):
        self.inscrire(donnees(accepte_conditions=True))
        self.assertEqual(Client.objects.get().consentement_version, '2027-01-15')

    def test_chaine_vraie_acceptee(self):
        self.assertEqual(self.inscrire(donnees(accepte_conditions='true')).status_code, 201)


class ConsentementDesComptesExistantsTests(APITestCase):
    def setUp(self):
        cache.clear()
        self.ancien = Client.objects.create(nom='Ancien', telephone='+237677830010', code_client='CL-CO01',
                                            telephone_verifie_le=timezone.now())      # créé avant l'obligation : jamais consenti
        self.ancien.set_password(MDP); self.ancien.save()
        self.admin = Administrateur.objects.create(nom='Admin', telephone='+237677830011')

    def session(self):
        api = APIClient()
        r = api.post('/api/auth/login/', {'telephone': '+237677830010', 'password': MDP, 'portail': 'client'}, format='json')
        self.assertEqual(r.status_code, 200, r.data)
        return api

    def test_un_ancien_compte_est_invite_a_consentir(self):
        me = self.session().get('/api/auth/me/').data
        self.assertEqual(me['role'], 'client')
        self.assertIs(me['consentement_a_jour'], False)

    def test_accepter_met_a_jour_la_preuve(self):
        api = self.session()
        r = api.post('/api/auth/me/consent/')
        self.assertEqual((r.status_code, r.data), (200, {'consentement_a_jour': True}))
        self.ancien.refresh_from_db()
        self.assertEqual(self.ancien.consentement_version, '2026-11-03')
        self.assertIsNotNone(self.ancien.consentement_le)
        self.assertIs(api.get('/api/auth/me/').data['consentement_a_jour'], True)

    def test_nouvelle_version_des_textes_redemande_le_consentement(self):
        api = self.session()
        api.post('/api/auth/me/consent/')
        with override_settings(CONSENT_VERSION='2027-06-01'):
            self.assertIs(api.get('/api/auth/me/').data['consentement_a_jour'], False)
            api.post('/api/auth/me/consent/')
            self.assertIs(api.get('/api/auth/me/').data['consentement_a_jour'], True)
        self.ancien.refresh_from_db()
        self.assertEqual(self.ancien.consentement_version, '2027-06-01')

    def test_reserve_aux_clients_connectes(self):
        self.assertEqual(APIClient().post('/api/auth/me/consent/').status_code, 401)
        staff = APIClient(); staff.force_authenticate(user=self.admin)
        self.assertEqual(staff.post('/api/auth/me/consent/').status_code, 403)

    def test_le_profil_du_personnel_n_expose_pas_ce_champ(self):
        staff = APIClient(); staff.force_authenticate(user=self.admin)
        self.assertNotIn('consentement_a_jour', staff.get('/api/auth/me/').data)

    def test_la_preuve_du_consentement_survit_a_la_suppression_du_compte(self):
        self.ancien.enregistrer_consentement()
        anonymiser_client(self.ancien)
        c = Client.objects.get(pk=self.ancien.pk)
        self.assertIsNotNone(c.consentement_le)                              # aucune donnée d'identité : on garde la preuve
        self.assertEqual(c.nom, 'Compte supprimé')
