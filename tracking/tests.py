import io
from PIL import Image
from django.core.files.uploadedfile import SimpleUploadedFile
from rest_framework.test import APITestCase, APIClient
from rest_framework import status

from users.models import Client, Administrateur, Agent
from shipments.models import Colis
from notifications.models import Notification
from .models import HistoriqueStatutColis, PhotoColis


def generer_image_test():
    buffer = io.BytesIO()
    Image.new('RGB', (10, 10), color='red').save(buffer, format='PNG')
    buffer.seek(0)
    return SimpleUploadedFile('test.png', buffer.read(), content_type='image/png')


class ChangerStatutTests(APITestCase):
    def setUp(self):
        self.client_user = Client.objects.create(nom='Client Test', telephone='+237677000040', code_client='CL-TRK01')
        self.client_user.set_password('pass1234')
        self.client_user.save()

        self.administrateur = Administrateur.objects.create(nom='Admin Test', telephone='+237677000041')
        self.administrateur.set_password('pass1234')
        self.administrateur.save()

        self.agent = Agent.objects.create(
            nom='Agent Test', telephone='+237677000042',
            localisation=Agent.Localisation.CAMEROUN, cree_par=self.administrateur,
        )
        self.agent.set_password('pass1234')
        self.agent.save()

        self.colis = Colis.objects.create(
            client=self.client_user, code_suivi='FM-TRK0001', mode_transport='maritime'
        )

    def test_agent_peut_changer_le_statut(self):
        api_client = APIClient()
        api_client.force_authenticate(user=self.agent)
        response = api_client.post(f'/api/colis/{self.colis.code_suivi}/statut/', {
            'statut': 'en_transit', 'note': 'Test',
        })
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.colis.refresh_from_db()
        self.assertEqual(self.colis.statut, 'en_transit')
        self.assertTrue(HistoriqueStatutColis.objects.filter(colis=self.colis, statut='en_transit').exists())

    def test_client_ne_peut_pas_changer_le_statut(self):
        api_client = APIClient()
        api_client.force_authenticate(user=self.client_user)
        response = api_client.post(f'/api/colis/{self.colis.code_suivi}/statut/', {'statut': 'en_transit'})
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_changement_statut_cree_une_notification(self):
        api_client = APIClient()
        api_client.force_authenticate(user=self.agent)
        api_client.post(f'/api/colis/{self.colis.code_suivi}/statut/', {'statut': 'livre'})
        self.assertTrue(
            Notification.objects.filter(utilisateur=self.client_user, colis=self.colis).exists()
        )


class PhotoColisTests(APITestCase):
    def setUp(self):
        self.administrateur = Administrateur.objects.create(nom='Admin Test', telephone='+237677000043')
        self.administrateur.set_password('pass1234')
        self.administrateur.save()

        self.agent = Agent.objects.create(
            nom='Agent Test', telephone='+237677000044',
            localisation=Agent.Localisation.CHINE, cree_par=self.administrateur,
        )
        self.agent.set_password('pass1234')
        self.agent.save()

        self.client_user = Client.objects.create(nom='Client Test', telephone='+237677000045', code_client='CL-TRK02')
        self.client_user.set_password('pass1234')
        self.client_user.save()

        self.colis = Colis.objects.create(
            client=self.client_user, code_suivi='FM-TRK0002', mode_transport='aerien_express'
        )

    def test_agent_peut_uploader_une_photo(self):
        api_client = APIClient()
        api_client.force_authenticate(user=self.agent)
        response = api_client.post(
            f'/api/colis/{self.colis.code_suivi}/photos/',
            {'photo': generer_image_test(), 'type_media': 'photo'},
            format='multipart',
        )
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertTrue(PhotoColis.objects.filter(colis=self.colis).exists())

    def test_client_ne_peut_pas_uploader_de_photo(self):
        api_client = APIClient()
        api_client.force_authenticate(user=self.client_user)
        response = api_client.post(
            f'/api/colis/{self.colis.code_suivi}/photos/',
            {'photo': generer_image_test()},
            format='multipart',
        )
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)