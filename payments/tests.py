from rest_framework.test import APITestCase, APIClient
from rest_framework import status
from users.models import Client, Administrateur
from shipments.models import Colis
from .models import Paiement


class PaiementTests(APITestCase):
    def setUp(self):
        self.client_user = Client.objects.create(
            nom='Client Test', telephone='+237677000020', code_client='CL-PAY01'
        )
        self.client_user.set_password('pass1234')
        self.client_user.save()

        self.administrateur = Administrateur.objects.create(nom='Admin Test', telephone='+237677000021')
        self.administrateur.set_password('pass1234')
        self.administrateur.save()

        self.colis = Colis.objects.create(
            client=self.client_user, code_suivi='FM-PAY0001', mode_transport='maritime'
        )

    def test_client_peut_creer_un_paiement(self):
        api_client = APIClient()
        api_client.force_authenticate(user=self.client_user)
        response = api_client.post('/api/paiements/', {
            'colis': self.colis.id, 'montant': '35000.00',
            'methode': 'mtn_momo', 'moment': 'en_ligne',
        })
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(response.data['statut'], 'en_attente')

    def test_administrateur_peut_confirmer_un_paiement(self):
        paiement = Paiement.objects.create(
            client=self.client_user, colis=self.colis, montant='35000.00',
            methode='mtn_momo', moment='en_ligne',
        )
        api_client = APIClient()
        api_client.force_authenticate(user=self.administrateur)
        response = api_client.post(f'/api/paiements/{paiement.id}/confirmer/')
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        paiement.refresh_from_db()
        self.assertEqual(paiement.statut, Paiement.Statut.PAYE)

    def test_client_ne_peut_pas_confirmer_un_paiement(self):
        paiement = Paiement.objects.create(
            client=self.client_user, colis=self.colis, montant='10000.00',
            methode='orange_money', moment='a_la_livraison',
        )
        api_client = APIClient()
        api_client.force_authenticate(user=self.client_user)
        response = api_client.post(f'/api/paiements/{paiement.id}/confirmer/')
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_client_ne_voit_que_ses_propres_paiements(self):
        autre_client = Client.objects.create(nom='Autre', telephone='+237677000022', code_client='CL-PAY02')
        autre_colis = Colis.objects.create(client=autre_client, code_suivi='FM-PAY0002', mode_transport='maritime')
        Paiement.objects.create(
            client=self.client_user, colis=self.colis, montant='5000.00',
            methode='especes', moment='a_la_livraison',
        )
        Paiement.objects.create(
            client=autre_client, colis=autre_colis, montant='7000.00',
            methode='especes', moment='a_la_livraison',
        )

        api_client = APIClient()
        api_client.force_authenticate(user=self.client_user)
        response = api_client.get('/api/paiements/')
        self.assertEqual(len(response.data['results']), 1)