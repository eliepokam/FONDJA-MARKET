from rest_framework.test import APITestCase
from rest_framework import status
from .models import Client, CodeOtp


class InscriptionOtpTests(APITestCase):
    def test_inscription_cree_un_client_et_un_otp(self):
        response = self.client.post('/api/auth/register/', {
            'nom': 'Jean Test',
            'telephone': '+237677000001',
            'email': 'jean@test.com',
            'password': 'MotDePasse123',
        })
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertTrue(Client.objects.filter(telephone='+237677000001').exists())
        self.assertTrue(CodeOtp.objects.filter(utilisateur__telephone='+237677000001').exists())

    def test_verification_otp_renvoie_des_tokens(self):
        self.client.post('/api/auth/register/', {
            'nom': 'Jean Test', 'telephone': '+237677000002',
            'email': 'jean2@test.com', 'password': 'MotDePasse123',
        })
        otp = CodeOtp.objects.get(utilisateur__telephone='+237677000002')

        response = self.client.post('/api/auth/otp/verify/', {
            'telephone': '+237677000002', 'code': otp.code,
        })
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertIn('access', response.data)

    def test_mauvais_code_otp_incremente_les_tentatives(self):
        self.client.post('/api/auth/register/', {
            'nom': 'Jean Test', 'telephone': '+237677000003',
            'email': 'jean3@test.com', 'password': 'MotDePasse123',
        })
        response = self.client.post('/api/auth/otp/verify/', {
            'telephone': '+237677000003', 'code': '000000',
        })
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        otp = CodeOtp.objects.get(utilisateur__telephone='+237677000003')
        self.assertEqual(otp.tentatives, 1)
# Create your tests here.
