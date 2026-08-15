from rest_framework.test import APITestCase, APIClient
from rest_framework import status
from users.models import Client, Administrateur, Agent
from .models import Colis


class ColisPermissionsTests(APITestCase):
    def setUp(self):
        self.client_user = Client.objects.create(
            nom='Client Test', telephone='+237677000010', code_client='CL-TEST01'
        )
        self.client_user.set_password('pass1234')
        self.client_user.save()

        self.administrateur = Administrateur.objects.create(nom='Admin Test', telephone='+237677000011')
        self.administrateur.set_password('pass1234')
        self.administrateur.save()

        self.agent = Agent.objects.create(
            nom='Agent Test', telephone='+237677000012',
            localisation=Agent.Localisation.CAMEROUN, cree_par=self.administrateur,
        )
        self.agent.set_password('pass1234')
        self.agent.save()

    def test_client_peut_creer_un_colis(self):
        api_client = APIClient()
        api_client.force_authenticate(user=self.client_user)
        response = api_client.post('/api/colis/', {
            'description': 'Test', 'mode_transport': 'aerien_standard', 'poids_kg': 2.5,
        })
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)

    def test_agent_ne_peut_pas_creer_de_colis(self):
        api_client = APIClient()
        api_client.force_authenticate(user=self.agent)
        response = api_client.post('/api/colis/', {
            'description': 'Test', 'mode_transport': 'aerien_standard', 'poids_kg': 2.5,
        })
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_anonyme_ne_peut_pas_lister_les_colis(self):
        response = APIClient().get('/api/colis/')
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_client_ne_voit_que_ses_propres_colis(self):
        autre_client = Client.objects.create(
            nom='Autre Client', telephone='+237677000013', code_client='CL-TEST02'
        )
        Colis.objects.create(client=self.client_user, code_suivi='FM-TEST0001', mode_transport='maritime')
        Colis.objects.create(client=autre_client, code_suivi='FM-TEST0002', mode_transport='maritime')

        api_client = APIClient()
        api_client.force_authenticate(user=self.client_user)
        response = api_client.get('/api/colis/')

        resultats = response.data['results']  # pagination active depuis le Jour 2
        self.assertEqual(len(resultats), 1)
        self.assertEqual(resultats[0]['code_suivi'], 'FM-TEST0001')
# Create your tests here.
