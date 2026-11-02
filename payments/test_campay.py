"""CamPay : client HTTP, sélecteur de prestataire, parcours complet et rattrapage. Aucun appel réseau réel."""
from decimal import Decimal
from io import StringIO
from unittest.mock import patch, MagicMock

import requests
from django.core.management import call_command
from django.test import override_settings
from rest_framework.test import APITestCase, APIClient

from notifications.models import Notification
from shipments.models import Colis
from users.models import Client
from .models import Paiement
from .services import FreeMoPayClient, FreeMoPayError, get_gateway
from .services_campay import CamPayClient

CAMPAY = dict(PAYMENT_PROVIDER='campay', CAMPAY_BASE_URL='https://demo.campay.test/',
              CAMPAY_APP_USERNAME='user', CAMPAY_APP_PASSWORD='pass')


def rep(status_code, corps):
    return MagicMock(status_code=status_code, content=b'x', text='x', json=lambda: corps)


def faux_campay(collect=None, token=None, statut=None):
    """Simule post() (token + collect) et get() (statut) de CamPay."""
    def post(url, **kw):
        if url.endswith('/api/token/'):
            return token or rep(200, {'token': 'TKN'})
        if url.endswith('/api/collect/'):
            return collect or rep(200, {'reference': 'CP-REF-1', 'ussd_code': '*126#', 'operator': 'MTN'})
        raise AssertionError(f'URL inattendue {url}')
    def get(url, **kw):
        return statut or rep(200, {'reference': 'CP-REF-1', 'status': 'PENDING'})
    return post, get


@override_settings(**CAMPAY)
class CamPayClientTests(APITestCase):
    def creer(self, post):
        with patch('payments.services_campay.requests.post', side_effect=post) as m:
            data = CamPayClient().create_payment(amount=Decimal('19250.00'), payer_phone='237677123456',
                                                 external_id='12', description='Colis FM-1', callback_url='x')
            return data, m

    def test_collecte_construit_la_bonne_requete(self):
        post, _ = faux_campay()
        data, m = self.creer(post)
        self.assertEqual(data['reference'], 'CP-REF-1')
        url_token, kw_token = m.call_args_list[0].args[0], m.call_args_list[0].kwargs
        self.assertEqual(url_token, 'https://demo.campay.test/api/token/')          # pas de double slash
        self.assertEqual(kw_token['json'], {'username': 'user', 'password': 'pass'})
        url_collect, kw = m.call_args_list[1].args[0], m.call_args_list[1].kwargs
        self.assertEqual(url_collect, 'https://demo.campay.test/api/collect/')
        self.assertEqual(kw['headers']['Authorization'], 'Token TKN')
        self.assertEqual(kw['json'], {'amount': '19250', 'currency': 'XAF', 'from': '237677123456',
                                      'description': 'Colis FM-1', 'external_reference': '12'})

    def test_numero_invalide_message_clair(self):
        post, _ = faux_campay(collect=rep(400, {'message': 'ER101 Invalid phone number'}))
        with self.assertRaisesMessage(FreeMoPayError, 'format 237XXXXXXXXX'):
            self.creer(post)

    def test_operateur_non_supporte(self):
        post, _ = faux_campay(collect=rep(400, {'message': 'ER102 Unsupported carrier'}))
        with self.assertRaisesMessage(FreeMoPayError, 'MTN et Orange'):
            self.creer(post)

    def test_refus_sans_code_connu_garde_le_message_du_prestataire(self):
        post, _ = faux_campay(collect=rep(400, {'message': 'Solde insuffisant'}))
        with self.assertRaisesMessage(FreeMoPayError, 'Solde insuffisant'):
            self.creer(post)

    def test_identifiants_refuses_donnent_une_erreur_de_configuration(self):
        post, _ = faux_campay(token=rep(401, {'detail': 'Invalid credentials'}))
        with self.assertRaisesMessage(FreeMoPayError, 'mal configuré'):
            self.creer(post)

    def test_reseau_coupe(self):
        with patch('payments.services_campay.requests.post', side_effect=requests.ConnectionError('boom')):
            with self.assertRaises(FreeMoPayError):
                CamPayClient().create_payment(amount=Decimal('1000'), payer_phone='237677123456',
                                              external_id='1', description='t')

    def test_statuts_normalises(self):
        for brut, attendu in (('SUCCESSFUL', 'SUCCESS'), ('FAILED', 'FAILED'), ('PENDING', 'PENDING')):
            _, get = faux_campay(statut=rep(200, {'reference': 'R', 'status': brut}))
            with patch('payments.services_campay.requests.post', side_effect=faux_campay()[0]), \
                 patch('payments.services_campay.requests.get', side_effect=get) as mget:
                self.assertEqual(CamPayClient().get_status('R')['status'], attendu)
                self.assertEqual(mget.call_args.args[0], 'https://demo.campay.test/api/transaction/R/')

    def test_statut_http_non_200(self):
        _, get = faux_campay(statut=rep(404, {}))
        with patch('payments.services_campay.requests.post', side_effect=faux_campay()[0]), \
             patch('payments.services_campay.requests.get', side_effect=get):
            with self.assertRaises(FreeMoPayError):
                CamPayClient().get_status('R')


class SelecteurDePrestataireTests(APITestCase):
    def test_freemopay_par_defaut(self):
        self.assertIsInstance(get_gateway(), FreeMoPayClient)

    @override_settings(**CAMPAY)
    def test_campay_quand_configure(self):
        self.assertIsInstance(get_gateway(), CamPayClient)


@override_settings(**CAMPAY)
class ParcoursCamPayTests(APITestCase):
    """Même parcours que le front, mais avec CamPay : créer -> initier -> sonder -> payé."""

    def setUp(self):
        self.client_user = Client.objects.create(nom='C', telephone='+237677000700', code_client='CL-CP01')
        self.colis = Colis.objects.create(client=self.client_user, code_suivi='FM-CP000001',
                                          mode_transport='maritime', cout_final=Decimal('19250'))
        self.api = APIClient(); self.api.force_authenticate(user=self.client_user)

    def creer_et_initier(self):
        r = self.api.post('/api/paiements/', {'colis': self.colis.id, 'methode': 'mtn_momo', 'moment': 'en_ligne',
                                              'telephone_paiement': '677123456'}, format='json')
        self.assertEqual(r.status_code, 201, r.data)
        post, _ = faux_campay()
        with patch('payments.services_campay.requests.post', side_effect=post) as m:
            r2 = self.api.post(f"/api/paiements/{r.data['id']}/initier/")
        return r.data['id'], r2, m

    def verifier(self, pid, statut):
        _, get = faux_campay(statut=rep(200, {'reference': 'CP-REF-1', 'status': statut}))
        with patch('payments.services_campay.requests.post', side_effect=faux_campay()[0]), \
             patch('payments.services_campay.requests.get', side_effect=get):
            return self.api.post(f'/api/paiements/{pid}/verifier/')

    def test_du_paiement_cree_au_paiement_confirme(self):
        pid, r, m = self.creer_et_initier()
        self.assertEqual(r.status_code, 200, r.data)
        self.assertEqual(m.call_args_list[1].kwargs['json']['from'], '237677123456')
        self.assertEqual(Paiement.objects.get(pk=pid).reference_prestataire, 'CP-REF-1')

        self.assertEqual(self.verifier(pid, 'PENDING').data['statut'], 'en_attente')
        self.assertEqual(self.verifier(pid, 'SUCCESSFUL').data['statut'], 'paye')
        self.assertTrue(Notification.objects.filter(utilisateur=self.client_user, contenu__contains='confirmé').exists())

    def test_echec_cote_prestataire(self):
        pid, _, _ = self.creer_et_initier()
        self.assertEqual(self.verifier(pid, 'FAILED').data['statut'], 'echoue')

    def test_refus_du_prestataire_arrive_en_phrase_claire(self):
        r = self.api.post('/api/paiements/', {'colis': self.colis.id, 'methode': 'mtn_momo', 'moment': 'en_ligne',
                                              'telephone_paiement': '677123456'}, format='json')
        post, _ = faux_campay(collect=rep(400, {'message': 'ER102 Unsupported'}))
        with patch('payments.services_campay.requests.post', side_effect=post):
            r2 = self.api.post(f"/api/paiements/{r.data['id']}/initier/")
        self.assertEqual(r2.status_code, 502)
        self.assertIn('MTN et Orange', r2.data['detail'])
        self.assertIsNone(Paiement.objects.get(pk=r.data['id']).reference_prestataire)


class ReconciliationTests(APITestCase):
    def setUp(self):
        self.client_user = Client.objects.create(nom='C', telephone='+237677000800', code_client='CL-RC01')
        self.colis = Colis.objects.create(client=self.client_user, code_suivi='FM-RC000001',
                                          mode_transport='maritime', cout_final=Decimal('5000'))

    def paiement(self, **kw):
        defaults = dict(client=self.client_user, colis=self.colis, montant='5000', methode='mtn_momo',
                        moment='en_ligne', reference_prestataire='REF-1')
        defaults.update(kw)
        p = Paiement.objects.create(**defaults)
        Paiement.objects.filter(pk=p.pk).update(cree_le=p.cree_le.replace(year=2020))   # vieux de plusieurs années
        return p

    def lancer(self, statut='SUCCESS'):
        out = StringIO()
        with patch('payments.views.get_gateway') as gw:
            gw.return_value.get_status.return_value = {'status': statut}
            call_command('reconcilier_paiements', stdout=out)
            return out.getvalue(), gw.return_value.get_status

    def test_rattrape_un_paiement_reste_en_attente(self):
        p = self.paiement()
        sortie, _ = self.lancer('SUCCESS')
        p.refresh_from_db()
        self.assertEqual(p.statut, 'paye')
        self.assertIn('1 mis à jour', sortie)

    def test_ignore_ce_qui_n_a_pas_de_reference_ou_est_deja_finalise_ou_trop_recent(self):
        self.paiement(reference_prestataire=None)
        self.paiement(statut='paye', reference_prestataire='REF-2')
        recent = Paiement.objects.create(client=self.client_user, colis=self.colis, montant='5000',
                                         methode='mtn_momo', moment='en_ligne', reference_prestataire='REF-3')
        _, get_status = self.lancer('SUCCESS')
        get_status.assert_not_called()
        recent.refresh_from_db()
        self.assertEqual(recent.statut, 'en_attente')

    def test_une_panne_prestataire_ne_casse_pas_la_commande(self):
        p = self.paiement()
        out = StringIO()
        with patch('payments.views.get_gateway') as gw:
            gw.return_value.get_status.side_effect = FreeMoPayError('injoignable')
            call_command('reconcilier_paiements', stdout=out)
        p.refresh_from_db()
        self.assertEqual(p.statut, 'en_attente')
        self.assertIn('1 erreur', out.getvalue())
