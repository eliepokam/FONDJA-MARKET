"""Preuves d'enregistrement de l'agent : photos ET vidéos."""
import io

from django.core.files.uploadedfile import SimpleUploadedFile
from PIL import Image
from rest_framework.test import APITestCase, APIClient

from shipments.models import Colis
from tracking.models import PhotoColis
from users.models import Administrateur, Agent, Client

MP4 = b'\x00\x00\x00\x18ftypmp42\x00\x00\x00\x00' + b'\x00' * 200
WEBM = b'\x1a\x45\xdf\xa3' + b'\x00' * 200


def png():
    tampon = io.BytesIO()
    Image.new('RGB', (4, 4)).save(tampon, format='PNG')
    return SimpleUploadedFile('p.png', tampon.getvalue())


class MediaAgentTests(APITestCase):
    def setUp(self):
        admin = Administrateur.objects.create(nom='Admin', telephone='+237677600001')
        self.agent = Agent.objects.create(nom='Agent', telephone='+237677600002', localisation='chine', cree_par=admin)
        self.client_user = Client.objects.create(nom='C', telephone='+237677600003', code_client='CL-MD01')
        self.colis = Colis.objects.create(client=self.client_user, code_suivi='FM-MD000001', mode_transport='maritime')
        self.url = f'/api/colis/{self.colis.code_suivi}/photos/'
        self.api = APIClient(); self.api.force_authenticate(user=self.agent)

    def envoyer(self, fichier, type_media):
        return self.api.post(self.url, {'photo': fichier, 'type_media': type_media}, format='multipart')

    def test_une_video_mp4_est_acceptee(self):
        r = self.envoyer(SimpleUploadedFile('v.mp4', MP4, content_type='video/mp4'), 'video')
        self.assertEqual(r.status_code, 201, r.data)
        self.assertEqual(PhotoColis.objects.get().type_media, 'video')

    def test_une_video_mov_ou_webm_est_acceptee(self):
        self.assertEqual(self.envoyer(SimpleUploadedFile('v.webm', WEBM), 'video').status_code, 201)

    def test_un_faux_fichier_video_est_refuse(self):
        r = self.envoyer(SimpleUploadedFile('v.mp4', b'<html>pas une video</html>' * 10), 'video')
        self.assertEqual(r.status_code, 400)
        self.assertFalse(PhotoColis.objects.exists())

    def test_une_image_declaree_comme_video_est_refusee(self):
        self.assertEqual(self.envoyer(png(), 'video').status_code, 400)

    def test_une_video_declaree_comme_photo_est_refusee(self):
        self.assertEqual(self.envoyer(SimpleUploadedFile('v.mp4', MP4), 'photo').status_code, 400)

    def test_la_photo_reste_acceptee(self):
        self.assertEqual(self.envoyer(png(), 'photo').status_code, 201)

    def test_la_liste_renvoie_les_deux_types(self):
        self.envoyer(png(), 'photo')
        self.envoyer(SimpleUploadedFile('v.mp4', MP4), 'video')
        types = sorted(m['type_media'] for m in self.api.get(self.url).data['results'])
        self.assertEqual(types, ['photo', 'video'])
