"""
Parcours de paiement tel que le front le déroule :
  créer le paiement (moyen + numéro choisis) -> initier -> sonder (verifier) -> payé / échoué.
Aucun appel réseau réel : FreeMoPay est mocké.
"""
from decimal import Decimal
from unittest.mock import patch

from rest_framework.test import APITestCase, APIClient

from notifications.models import Notification
from shipments.models import Colis, Tarif
from users.models import Client, Administrateur, Agent
from .models import Paiement
from .services import FreeMoPayError


def creer_client(tel, code):
    c = Client.objects.create(nom='Client', telephone=tel, code_client=code)
    c.set_password('x'); c.save()
    return c


def api_as(user):
    api = APIClient()
    api.force_authenticate(user=user)
    return api


class BaseParcours(APITestCase):
    def setUp(self):
        self.client_user = creer_client('+237677000300', 'CL-FR01')
        self.autre = creer_client('+237677000301', 'CL-FR02')
        self.colis = Colis.objects.create(client=self.client_user, code_suivi='FM-FR000001',
                                          mode_transport='aerien_standard', cout_final=Decimal('19250'))

    def creer(self, **extra):
        data = {'colis': self.colis.id, 'methode': 'mtn_momo', 'moment': 'en_ligne',
                'telephone_paiement': '677123456'}
        data.update(extra)
        return api_as(self.client_user).post('/api/paiements/', data, format='json')


class CreationPaiementTests(BaseParcours):
    def test_numero_local_normalise_en_e164_et_montant_serveur(self):
        r = self.creer(montant='1')
        self.assertEqual(r.status_code, 201, r.data)
        self.assertEqual(r.data['telephone_paiement'], '+237677123456')
        self.assertEqual(r.data['montant'], '19250.00')

    def test_numero_avec_indicatif_et_espaces(self):
        r = self.creer(telephone_paiement='+237 677 12 34 56')
        self.assertEqual(r.status_code, 201, r.data)
        self.assertEqual(r.data['telephone_paiement'], '+237677123456')

    def test_numero_obligatoire_pour_un_paiement_en_ligne(self):
        for valeur in (None, '', '   '):
            r = self.creer(telephone_paiement=valeur)
            self.assertEqual(r.status_code, 400, valeur)
            self.assertIn('telephone_paiement', r.data)
        data = {'colis': self.colis.id, 'methode': 'mtn_momo', 'moment': 'en_ligne'}
        r = api_as(self.client_user).post('/api/paiements/', data, format='json')
        self.assertEqual(r.status_code, 400)
        self.assertIn('telephone_paiement', r.data)

    def test_numeros_refuses(self):
        for numero in ('12345', '+33612345678', '222123456', 'abcdefghi', '+23767012'):
            r = self.creer(telephone_paiement=numero)
            self.assertEqual(r.status_code, 400, numero)
            self.assertIn('telephone_paiement', r.data, numero)

    def test_incoherence_moyen_de_paiement_et_operateur(self):
        r = self.creer(methode='mtn_momo', telephone_paiement='690123456')       # numéro Orange
        self.assertEqual(r.status_code, 400)
        r = self.creer(methode='orange_money', telephone_paiement='677123456')   # numéro MTN
        self.assertEqual(r.status_code, 400)
        r = self.creer(methode='orange_money', telephone_paiement='690123456')
        self.assertEqual(r.status_code, 201, r.data)

    def test_paiement_hors_ligne_ignore_le_numero(self):
        r = self.creer(methode='especes', moment='a_la_livraison', telephone_paiement='677123456')
        self.assertEqual(r.status_code, 201, r.data)
        self.assertIsNone(r.data['telephone_paiement'])

    def test_historique_filtre_par_colis(self):
        autre_colis = Colis.objects.create(client=self.client_user, code_suivi='FM-FR000002',
                                           mode_transport='maritime', cout_final=Decimal('5000'))
        Paiement.objects.create(client=self.client_user, colis=autre_colis, montant='5000',
                                methode='mtn_momo', moment='en_ligne', statut='echoue')
        self.creer()
        r = api_as(self.client_user).get(f'/api/paiements/?colis={self.colis.id}')
        self.assertEqual(len(r.data['results']), 1)
        self.assertEqual(r.data['results'][0]['colis'], self.colis.id)


class InitierEtVerifierTests(BaseParcours):
    def initier(self, paiement_id, reference='FMP-1'):
        with patch('payments.views.get_gateway') as mock_cls:
            mock_cls.return_value.create_payment.return_value = {'reference': reference}
            r = api_as(self.client_user).post(f'/api/paiements/{paiement_id}/initier/')
            return r, mock_cls.return_value.create_payment.call_args

    def verifier(self, user, paiement_id, statut_freemopay=None, erreur=None):
        with patch('payments.views.get_gateway') as mock_cls:
            if erreur:
                mock_cls.return_value.get_status.side_effect = erreur
            else:
                mock_cls.return_value.get_status.return_value = {'status': statut_freemopay}
            r = api_as(user).post(f'/api/paiements/{paiement_id}/verifier/')
            return r, mock_cls.return_value.get_status.call_count

    def test_initier_debite_le_numero_choisi_pas_le_numero_du_compte(self):
        pid = self.creer(telephone_paiement='699123456', methode='orange_money').data['id']
        r, appel = self.initier(pid)
        self.assertEqual(r.status_code, 200)
        self.assertEqual(appel.kwargs['payer_phone'], '237699123456')      # pas 237677000300
        self.assertEqual(appel.kwargs['amount'], Decimal('19250.00'))

    def test_paiement_ancien_sans_numero_retombe_sur_le_numero_du_compte(self):
        p = Paiement.objects.create(client=self.client_user, colis=self.colis, montant='19250',
                                    methode='mtn_momo', moment='en_ligne')
        r, appel = self.initier(p.id)
        self.assertEqual(appel.kwargs['payer_phone'], '237677000300')

    def test_verifier_passe_a_paye_sans_callback(self):
        pid = self.creer().data['id']
        self.initier(pid)
        r, _ = self.verifier(self.client_user, pid, 'SUCCESS')
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.data['statut'], 'paye')
        self.assertIsNotNone(Paiement.objects.get(pk=pid).paye_le)
        notif = Notification.objects.get(utilisateur=self.client_user, type='paiement')
        self.assertIn('19 250 XAF', notif.contenu)
        self.assertIn('confirmé', notif.contenu)

    def test_verifier_passe_a_echoue_et_libere_le_colis(self):
        pid = self.creer().data['id']
        self.initier(pid)
        r, _ = self.verifier(self.client_user, pid, 'FAILED')
        self.assertEqual(r.data['statut'], 'echoue')
        self.assertTrue(Notification.objects.filter(utilisateur=self.client_user, contenu__contains='échoué').exists())
        self.assertEqual(self.creer().status_code, 201)       # le client peut réessayer

    def test_verifier_pending_ne_change_rien(self):
        pid = self.creer().data['id']
        self.initier(pid)
        r, _ = self.verifier(self.client_user, pid, 'PENDING')
        self.assertEqual(r.data['statut'], 'en_attente')
        self.assertFalse(Notification.objects.filter(type='paiement').exists())

    def test_verifier_sans_reference_n_appelle_pas_freemopay(self):
        pid = self.creer().data['id']              # créé mais jamais initié
        r, appels = self.verifier(self.client_user, pid, 'SUCCESS')
        self.assertEqual(appels, 0)
        self.assertEqual(r.data['statut'], 'en_attente')

    def test_verifier_est_idempotent(self):
        pid = self.creer().data['id']
        self.initier(pid)
        self.verifier(self.client_user, pid, 'SUCCESS')
        r, appels = self.verifier(self.client_user, pid, 'FAILED')   # paiement déjà payé : plus d'appel
        self.assertEqual(appels, 0)
        self.assertEqual(r.data['statut'], 'paye')
        self.assertEqual(Notification.objects.filter(type='paiement').count(), 1)

    def test_verifier_502_si_freemopay_injoignable(self):
        pid = self.creer().data['id']
        self.initier(pid)
        r, _ = self.verifier(self.client_user, pid, erreur=FreeMoPayError('injoignable'))
        self.assertEqual(r.status_code, 502)
        self.assertEqual(Paiement.objects.get(pk=pid).statut, 'en_attente')

    def test_un_autre_client_ne_peut_pas_verifier(self):
        pid = self.creer().data['id']
        self.initier(pid)
        r, appels = self.verifier(self.autre, pid, 'SUCCESS')
        self.assertEqual(r.status_code, 404)
        self.assertEqual(appels, 0)

    def test_le_webhook_notifie_aussi(self):
        pid = self.creer().data['id']
        self.initier(pid, reference='FMP-WH')
        with patch('payments.views.get_gateway') as mock_cls:
            mock_cls.return_value.get_status.return_value = {'status': 'SUCCESS'}
            r = APIClient().post('/api/webhooks/freemopay/', {'reference': 'FMP-WH'}, format='json')
        self.assertEqual(r.status_code, 200)
        self.assertEqual(Paiement.objects.get(pk=pid).statut, 'paye')
        self.assertEqual(Notification.objects.filter(type='paiement').count(), 1)


class ParcoursCompletTest(APITestCase):
    """Déclaration -> pesée par l'agent (PATCH) -> notification -> paiement -> payé -> coût verrouillé."""

    def test_de_la_declaration_au_paiement_confirme(self):
        admin = Administrateur.objects.create(nom='Admin', telephone='+237677000310'); admin.set_password('x'); admin.save()
        agent = Agent.objects.create(nom='Agent', telephone='+237677000311', localisation='chine', cree_par=admin)
        agent.set_password('x'); agent.save()
        client = creer_client('+237677000312', 'CL-FR10')
        Tarif.objects.create(mode_transport='maritime', prix_par_m3=Decimal('250000'), prix_par_kg=Decimal('1000'))

        # 1. le client déclare son colis (valeurs déclarées seulement) : aucun coût, impossible de payer
        r = api_as(client).post('/api/colis/', {'description': 'Meubles', 'mode_transport': 'maritime',
                                                'poids_declare_kg': '30', 'volume_declare_m3': '0.2'}, format='json')
        self.assertEqual(r.status_code, 201, r.data)
        colis_id = r.data['id']
        self.assertIsNone(r.data['cout_final'])
        demande = {'colis': colis_id, 'methode': 'mtn_momo', 'moment': 'en_ligne', 'telephone_paiement': '677123456'}
        self.assertEqual(api_as(client).post('/api/paiements/', demande, format='json').status_code, 400)

        # 2. l'agent confirme les mesures à l'entrepôt : coût automatique + notification
        r = api_as(agent).patch(f'/api/colis/{colis_id}/', {'poids_kg': '28', 'volume_m3': '0.18'}, format='json')
        self.assertEqual(r.status_code, 200, r.data)
        self.assertEqual(Colis.objects.get(pk=colis_id).cout_final, Decimal('45000'))     # 0.18 m3 x 250000
        self.assertTrue(Notification.objects.filter(utilisateur=client, type='paiement',
                                                    contenu__contains='45 000 XAF').exists())

        # 3. le client choisit son moyen et son numéro : montant imposé par le serveur
        r = api_as(client).post('/api/paiements/', {**demande, 'montant': '1'}, format='json')
        self.assertEqual(r.status_code, 201, r.data)
        pid = r.data['id']
        self.assertEqual(r.data['montant'], '45000.00')

        # 4. initiation puis sondage : confirmé sans aucun callback
        with patch('payments.views.get_gateway') as mock_cls:
            mock_cls.return_value.create_payment.return_value = {'reference': 'FMP-E2E'}
            self.assertEqual(api_as(client).post(f'/api/paiements/{pid}/initier/').status_code, 200)
            mock_cls.return_value.get_status.return_value = {'status': 'SUCCESS'}
            r = api_as(client).post(f'/api/paiements/{pid}/verifier/')
        self.assertEqual(r.data['statut'], 'paye')

        # 5. le coût est verrouillé : l'agent ne peut plus changer les mesures
        r = api_as(agent).patch(f'/api/colis/{colis_id}/', {'volume_m3': '0.01'}, format='json')
        self.assertEqual(r.status_code, 400)
        self.assertEqual(Colis.objects.get(pk=colis_id).cout_final, Decimal('45000'))
