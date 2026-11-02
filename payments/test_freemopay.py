"""
Tests du flux FreeMoPay (initiation + webhook) et durcissement sécurité.

Aucun appel réseau réel : FreeMoPayClient / requests sont remplacés par des mocks.
Lancer :  python manage.py test payments
"""
from decimal import Decimal
from unittest.mock import patch, MagicMock

import requests
from django.test import override_settings
from rest_framework import status
from rest_framework.test import APITestCase, APIClient

from shipments.models import Colis
from users.models import Client, Administrateur
from .models import Paiement
from .services import FreeMoPayClient, FreeMoPayError

WEBHOOK_URL = '/api/webhooks/freemopay/'


def creer_client(telephone, code):
    c = Client.objects.create(nom='Client Test', telephone=telephone, code_client=code)
    c.set_password('pass1234')
    c.save()
    return c


def creer_admin(telephone):
    a = Administrateur.objects.create(nom='Admin Test', telephone=telephone)
    a.set_password('pass1234')
    a.save()
    return a


def api_as(user):
    api = APIClient()
    api.force_authenticate(user=user)
    return api


class BasePaiementTest(APITestCase):
    def setUp(self):
        self.client_user = creer_client('+237677000020', 'CL-PAY01')
        self.autre_client = creer_client('+237677000022', 'CL-PAY02')
        self.admin = creer_admin('+237677000021')
        self.colis = Colis.objects.create(
            client=self.client_user, code_suivi='FM-PAY0001', mode_transport='maritime',
            cout_final=Decimal('35000.00'),
        )
        self.colis_autre = Colis.objects.create(
            client=self.autre_client, code_suivi='FM-PAY0002', mode_transport='maritime',
        )

    def paiement(self, **kw):
        defaults = dict(client=self.client_user, colis=self.colis, montant=Decimal('35000.00'),
                        methode='mtn_momo', moment='en_ligne')
        defaults.update(kw)
        return Paiement.objects.create(**defaults)


# ---------------------------------------------------------------- initier
class InitierPaiementTests(BasePaiementTest):
    @patch('payments.views.get_gateway')
    def test_initier_envoie_la_demande_et_garde_la_reference(self, mock_cls):
        mock_cls.return_value.create_payment.return_value = {'reference': 'FMP-REF-1', 'status': 'PENDING'}
        p = self.paiement()

        r = api_as(self.client_user).post(f'/api/paiements/{p.id}/initier/')

        self.assertEqual(r.status_code, status.HTTP_200_OK)
        kwargs = mock_cls.return_value.create_payment.call_args.kwargs
        self.assertEqual(kwargs['payer_phone'], '237677000020')      # sans le "+"
        self.assertEqual(kwargs['external_id'], str(p.pk))
        self.assertEqual(kwargs['amount'], Decimal('35000.00'))
        p.refresh_from_db()
        self.assertEqual(p.reference_prestataire, 'FMP-REF-1')
        self.assertEqual(p.statut, Paiement.Statut.EN_ATTENTE)       # PAS payé tant que le webhook n'a pas confirmé

    @patch('payments.views.get_gateway')
    def test_initier_refuse_un_paiement_a_la_livraison(self, mock_cls):
        p = self.paiement(methode='especes', moment='a_la_livraison')
        r = api_as(self.client_user).post(f'/api/paiements/{p.id}/initier/')
        self.assertEqual(r.status_code, status.HTTP_400_BAD_REQUEST)
        mock_cls.return_value.create_payment.assert_not_called()

    @patch('payments.views.get_gateway')
    def test_initier_refuse_un_paiement_deja_paye(self, mock_cls):
        p = self.paiement(statut=Paiement.Statut.PAYE)
        r = api_as(self.client_user).post(f'/api/paiements/{p.id}/initier/')
        self.assertEqual(r.status_code, status.HTTP_400_BAD_REQUEST)
        mock_cls.return_value.create_payment.assert_not_called()

    @patch('payments.views.get_gateway')
    def test_initier_renvoie_502_si_freemopay_echoue(self, mock_cls):
        mock_cls.return_value.create_payment.side_effect = FreeMoPayError('Service indisponible')
        p = self.paiement()
        r = api_as(self.client_user).post(f'/api/paiements/{p.id}/initier/')
        self.assertEqual(r.status_code, status.HTTP_502_BAD_GATEWAY)
        p.refresh_from_db()
        self.assertIsNone(p.reference_prestataire)

    @patch('payments.views.get_gateway')
    def test_un_admin_ne_peut_pas_initier(self, mock_cls):
        p = self.paiement()
        r = api_as(self.admin).post(f'/api/paiements/{p.id}/initier/')
        self.assertEqual(r.status_code, status.HTTP_403_FORBIDDEN)

    @patch('payments.views.get_gateway')
    def test_un_client_ne_peut_pas_initier_le_paiement_d_un_autre(self, mock_cls):
        p = self.paiement()
        r = api_as(self.autre_client).post(f'/api/paiements/{p.id}/initier/')
        self.assertEqual(r.status_code, status.HTTP_404_NOT_FOUND)
        mock_cls.return_value.create_payment.assert_not_called()


# ---------------------------------------------------------------- webhook
class WebhookFreeMoPayTests(BasePaiementTest):
    def setUp(self):
        super().setUp()
        self.p = self.paiement(reference_prestataire='FMP-REF-1')

    def appeler(self, statut_reel, payload=None):
        with patch('payments.views.get_gateway') as mock_cls:
            mock_cls.return_value.get_status.return_value = {'status': statut_reel}
            return APIClient().post(WEBHOOK_URL, payload or {'reference': 'FMP-REF-1'}, format='json')

    def test_success_passe_le_paiement_a_paye(self):
        r = self.appeler('SUCCESS')
        self.assertEqual(r.status_code, 200)
        self.p.refresh_from_db()
        self.assertEqual(self.p.statut, Paiement.Statut.PAYE)
        self.assertIsNotNone(self.p.paye_le)

    def test_failed_passe_le_paiement_a_echoue(self):
        self.appeler('FAILED')
        self.p.refresh_from_db()
        self.assertEqual(self.p.statut, Paiement.Statut.ECHOUE)
        self.assertIsNone(self.p.paye_le)

    def test_pending_ne_change_rien(self):
        self.appeler('PENDING')
        self.p.refresh_from_db()
        self.assertEqual(self.p.statut, Paiement.Statut.EN_ATTENTE)

    def test_le_statut_du_payload_est_ignore(self):
        """Faux webhook annonçant SUCCESS alors que FreeMoPay (authentifié) dit FAILED."""
        self.appeler('FAILED', payload={'reference': 'FMP-REF-1', 'status': 'SUCCESS'})
        self.p.refresh_from_db()
        self.assertEqual(self.p.statut, Paiement.Statut.ECHOUE)

    def test_idempotent_si_appele_deux_fois(self):
        self.appeler('SUCCESS')
        premier_paye_le = Paiement.objects.get(pk=self.p.pk).paye_le
        r = self.appeler('SUCCESS')
        self.assertEqual(r.status_code, 200)
        self.assertEqual(Paiement.objects.get(pk=self.p.pk).paye_le, premier_paye_le)

    def test_reference_inconnue_repond_200_sans_rien_modifier(self):
        r = self.appeler('SUCCESS', payload={'reference': 'INCONNUE'})
        self.assertEqual(r.status_code, 200)
        self.p.refresh_from_db()
        self.assertEqual(self.p.statut, Paiement.Statut.EN_ATTENTE)

    def test_payload_sans_reference_repond_400(self):
        r = APIClient().post(WEBHOOK_URL, {}, format='json')
        self.assertEqual(r.status_code, 400)

    def test_statut_freemopay_inconnu_repond_400(self):
        r = self.appeler('BIZARRE')
        self.assertEqual(r.status_code, 400)

    def test_webhook_502_si_freemopay_injoignable(self):
        with patch('payments.views.get_gateway') as mock_cls:
            mock_cls.return_value.get_status.side_effect = FreeMoPayError('injoignable')
            r = APIClient().post(WEBHOOK_URL, {'reference': 'FMP-REF-1'}, format='json')
        self.assertEqual(r.status_code, 502)

    def test_le_webhook_n_est_pas_limite_par_le_throttle_anonyme(self):
        """Le throttle global (20/h pour les anonymes) ne doit JAMAIS bloquer FreeMoPay."""
        codes = set()
        with patch('payments.views.get_gateway') as mock_cls:
            mock_cls.return_value.get_status.return_value = {'status': 'PENDING'}
            for _ in range(30):
                codes.add(APIClient().post(WEBHOOK_URL, {'reference': 'FMP-REF-1'}, format='json').status_code)
        self.assertNotIn(429, codes)


# ---------------------------------------------------------------- client HTTP
@override_settings(FREEMOPAY_BASE_URL='https://api.freemopay.test/', FREEMOPAY_APP_KEY='app', FREEMOPAY_SECRET_KEY='sec')
class FreeMoPayClientTests(APITestCase):
    @patch('payments.services.requests.post')
    def test_create_payment_construit_la_bonne_requete(self, mock_post):
        mock_post.return_value = MagicMock(status_code=200, content=b'{}', json=lambda: {'reference': 'R1'})
        data = FreeMoPayClient().create_payment(
            amount=Decimal('35000.00'), payer_phone='237677000020', external_id='12',
            description='Test', callback_url='https://fondja.test/cb/',
        )
        self.assertEqual(data['reference'], 'R1')
        args, kwargs = mock_post.call_args
        self.assertEqual(args[0], 'https://api.freemopay.test/api/v2/payment')   # pas de double slash
        self.assertEqual(kwargs['json']['payer'], '237677000020')
        self.assertEqual(kwargs['json']['externalId'], '12')
        self.assertEqual(kwargs['json']['callback'], 'https://fondja.test/cb/')
        self.assertEqual((kwargs['auth'].username, kwargs['auth'].password), ('app', 'sec'))
        self.assertIsInstance(kwargs['json']['amount'], str)

    @patch('payments.services.requests.post')
    def test_create_payment_montant_en_xaf_sans_decimales(self, mock_post):
        """XAF n'a pas de centimes : on doit envoyer \"35000\" et non \"35000.00\"."""
        mock_post.return_value = MagicMock(status_code=200, content=b'{}', json=lambda: {'reference': 'R1'})
        FreeMoPayClient().create_payment(amount=Decimal('35000.00'), payer_phone='2376', external_id='1',
                                         description='t', callback_url='https://x/')
        self.assertEqual(mock_post.call_args.kwargs['json']['amount'], '35000')

    @patch('payments.services.requests.post')
    def test_create_payment_remonte_le_message_d_erreur(self, mock_post):
        mock_post.return_value = MagicMock(status_code=400, content=b'x', json=lambda: {'message': 'Solde insuffisant'})
        with self.assertRaisesMessage(FreeMoPayError, 'Solde insuffisant'):
            FreeMoPayClient().create_payment(amount=Decimal('1'), payer_phone='1', external_id='1',
                                             description='t', callback_url='https://x/')

    @patch('payments.services.requests.post', side_effect=requests.ConnectionError('boom'))
    def test_create_payment_reseau_coupe(self, _):
        with self.assertRaises(FreeMoPayError):
            FreeMoPayClient().create_payment(amount=Decimal('1'), payer_phone='1', external_id='1',
                                             description='t', callback_url='https://x/')

    @patch('payments.services.requests.get')
    def test_get_status_non_200_leve_une_erreur(self, mock_get):
        mock_get.return_value = MagicMock(status_code=404)
        with self.assertRaises(FreeMoPayError):
            FreeMoPayClient().get_status('REF')


# ---------------------------------------------------------------- sécurité
class DurcissementPaiementTests(BasePaiementTest):
    """Ces tests décrivent ce qu'un client malveillant NE DOIT PAS pouvoir faire."""

    def test_un_client_ne_peut_pas_payer_le_colis_d_un_autre(self):
        r = api_as(self.client_user).post('/api/paiements/', {
            'colis': self.colis_autre.id, 'montant': '1000.00', 'methode': 'mtn_momo', 'moment': 'en_ligne',
        })
        self.assertEqual(r.status_code, status.HTTP_400_BAD_REQUEST)

    def test_un_client_ne_peut_pas_modifier_le_montant_apres_creation(self):
        p = self.paiement()
        r = api_as(self.client_user).patch(f'/api/paiements/{p.id}/', {'montant': '1.00'}, format='json')
        self.assertIn(r.status_code, (status.HTTP_403_FORBIDDEN, status.HTTP_405_METHOD_NOT_ALLOWED))
        p.refresh_from_db()
        self.assertEqual(p.montant, Decimal('35000.00'))

    def test_un_client_ne_peut_pas_supprimer_un_paiement(self):
        p = self.paiement(statut=Paiement.Statut.PAYE)
        r = api_as(self.client_user).delete(f'/api/paiements/{p.id}/')
        self.assertIn(r.status_code, (status.HTTP_403_FORBIDDEN, status.HTTP_405_METHOD_NOT_ALLOWED))
        self.assertTrue(Paiement.objects.filter(pk=p.pk).exists())

    @patch('payments.views.get_gateway')
    def test_double_initiation_n_envoie_pas_deux_demandes_ussd(self, mock_cls):
        mock_cls.return_value.create_payment.return_value = {'reference': 'FMP-REF-1'}
        p = self.paiement()
        api = api_as(self.client_user)
        api.post(f'/api/paiements/{p.id}/initier/')
        r2 = api.post(f'/api/paiements/{p.id}/initier/')
        self.assertEqual(r2.status_code, status.HTTP_409_CONFLICT)
        self.assertEqual(mock_cls.return_value.create_payment.call_count, 1)

    def test_un_paiement_deja_paye_ne_repasse_pas_a_echoue(self):
        p = self.paiement(reference_prestataire='FMP-REF-9', statut=Paiement.Statut.PAYE)
        with patch('payments.views.get_gateway') as mock_cls:
            mock_cls.return_value.get_status.return_value = {'status': 'FAILED'}
            APIClient().post(WEBHOOK_URL, {'reference': 'FMP-REF-9'}, format='json')
        p.refresh_from_db()
        self.assertEqual(p.statut, Paiement.Statut.PAYE)

    def test_webhook_robuste_face_a_un_corps_malforme(self):
        for corps in ([1, 2, 3], {'reference': {'a': 1}}, {'reference': 12345}):
            r = APIClient().post(WEBHOOK_URL, corps, format='json')
            self.assertLess(r.status_code, 500, corps)