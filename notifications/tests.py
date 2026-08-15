from rest_framework.test import APITestCase, APIClient
from rest_framework import status
from users.models import Client
from .models import Notification


class NotificationsTests(APITestCase):
    def setUp(self):
        self.client_user = Client.objects.create(nom='Client Test', telephone='+237677000050', code_client='CL-NOT01')
        self.client_user.set_password('pass1234')
        self.client_user.save()

        self.autre_client = Client.objects.create(nom='Autre Client', telephone='+237677000051', code_client='CL-NOT02')
        self.autre_client.set_password('pass1234')
        self.autre_client.save()

        Notification.objects.create(utilisateur=self.client_user, type='general', canal='push', contenu='Pour moi')
        Notification.objects.create(utilisateur=self.autre_client, type='general', canal='push', contenu='Pas pour moi')

    def test_client_ne_voit_que_ses_notifications(self):
        api_client = APIClient()
        api_client.force_authenticate(user=self.client_user)
        response = api_client.get('/api/notifications/')
        resultats = response.data['results']
        self.assertEqual(len(resultats), 1)
        self.assertEqual(resultats[0]['contenu'], 'Pour moi')

    def test_anonyme_ne_peut_pas_lister_les_notifications(self):
        response = APIClient().get('/api/notifications/')
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)