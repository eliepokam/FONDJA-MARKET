"""Les refus de FreeMoPay doivent arriver au client sous forme de phrase claire, jamais de dict brut."""
from decimal import Decimal
from unittest.mock import patch, MagicMock

from rest_framework.test import APITestCase, APIClient

from shipments.models import Colis
from users.models import Client
from .models import Paiement
from .services import FreeMoPayClient, FreeMoPayError


def reponse(status_code, corps):
    return MagicMock(status_code=status_code, content=b'x', text='x', json=lambda: corps)


def creer():
    return FreeMoPayClient().create_payment(amount=Decimal('1000'), payer_phone='237677123456',
                                            external_id='1', description='t', callback_url='https://x/')


class MessagesErreurTests(APITestCase):
    @patch('payments.services.requests.post')
    def test_maintenance_cashin_desactive(self, mock_post):
        mock_post.return_value = reponse(403, {'code': 'CASHIN_DISABLED',
                                               'message': {'fr': 'Maintenance', 'en': 'Maintenance'}})
        with self.assertRaises(FreeMoPayError) as ctx:
            creer()
        self.assertEqual(ctx.exception.code, 'CASHIN_DISABLED')
        self.assertEqual(ctx.exception.status_code, 403)
        self.assertIn('momentanément indisponible', str(ctx.exception))
        self.assertNotIn('{', str(ctx.exception))                      # jamais un dict affiché

    @patch('payments.services.requests.post')
    def test_message_en_dictionnaire_de_langues(self, mock_post):
        mock_post.return_value = reponse(400, {'code': 'X', 'message': {'fr': 'Numéro invalide', 'en': 'Invalid'}})
        with self.assertRaisesMessage(FreeMoPayError, 'Numéro invalide'):
            creer()

    @patch('payments.services.requests.post')
    def test_message_texte_simple(self, mock_post):
        mock_post.return_value = reponse(400, {'message': 'Solde insuffisant'})
        with self.assertRaisesMessage(FreeMoPayError, 'Solde insuffisant'):
            creer()

    @patch('payments.services.requests.post')
    def test_corps_inexploitable_donne_un_message_generique(self, mock_post):
        for corps in ({}, [], {'message': {}}, {'message': 123}):
            mock_post.return_value = reponse(500, corps)
            with self.assertRaisesMessage(FreeMoPayError, 'FreeMoPay a refusé la demande de paiement.'):
                creer()


class InitierEnMaintenanceTests(APITestCase):
    def test_le_client_voit_une_phrase_claire_et_peut_reessayer(self):
        client = Client.objects.create(nom='C', telephone='+237677000500', code_client='CL-MT99')
        colis = Colis.objects.create(client=client, code_suivi='FM-MAINT001', mode_transport='maritime',
                                     cout_final=Decimal('5000'))
        p = Paiement.objects.create(client=client, colis=colis, montant='5000', methode='mtn_momo',
                                    moment='en_ligne', telephone_paiement='+237677123456')
        api = APIClient(); api.force_authenticate(user=client)

        with patch('payments.services.requests.post') as mock_post:
            mock_post.return_value = reponse(403, {'code': 'CASHIN_DISABLED', 'message': {'fr': 'Maintenance'}})
            r = api.post(f'/api/paiements/{p.id}/initier/')
        self.assertEqual(r.status_code, 502)
        self.assertIn('momentanément indisponible', r.data['detail'])
        p.refresh_from_db()
        self.assertIsNone(p.reference_prestataire)                     # rien d'envoyé : on peut renvoyer la demande

        with patch('payments.services.requests.post') as mock_post:
            mock_post.return_value = reponse(200, {'reference': 'FMP-OK'})
            r = api.post(f'/api/paiements/{p.id}/initier/')            # la maintenance est terminée
        self.assertEqual(r.status_code, 200)
        p.refresh_from_db()
        self.assertEqual(p.reference_prestataire, 'FMP-OK')
