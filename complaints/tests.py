from rest_framework.test import APITestCase, APIClient
from rest_framework import status
from users.models import Client, Administrateur, Agent
from shipments.models import Colis
from .models import Reclamation


class ReclamationTests(APITestCase):
    def setUp(self):
        self.client_user = Client.objects.create(
            nom='Client Test', telephone='+237677000030', code_client='CL-REC01'
        )
        self.client_user.set_password('pass1234')
        self.client_user.save()

        self.administrateur = Administrateur.objects.create(nom='Admin Test', telephone='+237677000031')
        self.administrateur.set_password('pass1234')
        self.administrateur.save()

        self.agent = Agent.objects.create(
            nom='Agent Test', telephone='+237677000032',
            localisation=Agent.Localisation.CAMEROUN, cree_par=self.administrateur,
        )
        self.agent.set_password('pass1234')
        self.agent.save()

        self.colis = Colis.objects.create(
            client=self.client_user, code_suivi='FM-REC0001', mode_transport='maritime'
        )

    def test_client_peut_creer_une_reclamation(self):
        api_client = APIClient()
        api_client.force_authenticate(user=self.client_user)
        response = api_client.post('/api/reclamations/', {
            'colis': self.colis.id, 'type': 'retard', 'description': 'Colis en retard de 3 jours',
        })
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)

    def test_agent_ne_peut_pas_creer_de_reclamation(self):
        api_client = APIClient()
        api_client.force_authenticate(user=self.agent)
        response = api_client.post('/api/reclamations/', {
            'colis': self.colis.id, 'type': 'retard', 'description': 'Test',
        })
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_client_peut_ajouter_un_message(self):
        reclamation = Reclamation.objects.create(
            client=self.client_user, colis=self.colis, type='retard', description='Test',
        )
        api_client = APIClient()
        api_client.force_authenticate(user=self.client_user)
        response = api_client.post(f'/api/reclamations/{reclamation.id}/messages/', {
            'message': 'Toujours pas de nouvelles',
        })
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)

    def test_liste_reclamations_inclut_les_messages(self):
        reclamation = Reclamation.objects.create(
            client=self.client_user, colis=self.colis, type='retard', description='Test',
        )
        api_client = APIClient()
        api_client.force_authenticate(user=self.client_user)
        api_client.post(f'/api/reclamations/{reclamation.id}/messages/', {'message': 'Un message'})

        response = api_client.get('/api/reclamations/')
        resultats = response.data['results']
        self.assertEqual(len(resultats[0]['messages']), 1)
        self.assertEqual(resultats[0]['messages'][0]['message'], 'Un message')

    def test_anonyme_ne_peut_pas_ajouter_de_message(self):
        reclamation = Reclamation.objects.create(
            client=self.client_user, colis=self.colis, type='retard', description='Test',
        )
        response = APIClient().post(f'/api/reclamations/{reclamation.id}/messages/', {'message': 'Test'})
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)