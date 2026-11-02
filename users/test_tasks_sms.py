from unittest.mock import patch, MagicMock

from django.test import SimpleTestCase, override_settings

from users.tasks import DevCodeSMSError, _envoyer_sms_devcode, envoyer_otp_task


class EnvoiSmsDevCodeTests(SimpleTestCase):
    @patch('users.tasks.requests.post')
    def test_sms_envoye_avec_les_bons_champs(self, mock_post):
        mock_post.return_value = MagicMock(status_code=200, text='{}', json=lambda: {'status': 200})
        _envoyer_sms_devcode('+237677123456', 'Bonjour')
        corps = mock_post.call_args.kwargs['json']
        self.assertEqual(corps['phone'], '+237677123456')
        self.assertEqual(corps['message'], 'Bonjour')
        self.assertEqual(corps['sender'], 'FondjaMkt')

    @patch('users.tasks.requests.post')
    def test_erreur_devcode_remontee(self, mock_post):
        mock_post.return_value = MagicMock(status_code=402, text='x',
                                           json=lambda: {'status': 402, 'code': 'insufficient_balance', 'message': 'Solde'})
        with self.assertRaises(DevCodeSMSError):
            _envoyer_sms_devcode('+237677123456', 'Bonjour')

    @override_settings(DISABLE_EXTERNAL_MESSAGING=False)
    @patch('users.tasks._envoyer_sms_devcode')
    def test_la_tache_utilise_bien_le_canal_sms(self, mock_sms):
        envoyer_otp_task('+237677123456', '123456', 'sms')
        mock_sms.assert_called_once()

    @patch('users.tasks._envoyer_sms_devcode')
    def test_aucun_envoi_reel_pendant_les_tests(self, mock_sms):
        envoyer_otp_task('+237677123456', '123456', 'sms')   # DISABLE_EXTERNAL_MESSAGING est actif sous `manage.py test`
        mock_sms.assert_not_called()
