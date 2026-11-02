"""Notifications côté client : routes réellement appelées par le front + envoi push."""
from unittest.mock import patch

from django.test import TestCase, override_settings
from rest_framework.test import APITestCase, APIClient

from notifications.models import FCMToken, Notification
from notifications.services import notify
from notifications.tasks import envoyer_push_task
from shipments.models import Colis
from users.models import Client


def api_as(user):
    api = APIClient()
    api.force_authenticate(user=user)
    return api


class RoutesNotificationsTests(APITestCase):
    def setUp(self):
        self.alice = Client.objects.create(nom='Alice', telephone='+237677300001', code_client='CL-NT01')
        self.bob = Client.objects.create(nom='Bob', telephone='+237677300002', code_client='CL-NT02')
        self.n1 = Notification.objects.create(utilisateur=self.alice, type='general', canal='push', contenu='Un')
        self.n2 = Notification.objects.create(utilisateur=self.alice, type='general', canal='push', contenu='Deux')
        self.n_bob = Notification.objects.create(utilisateur=self.bob, type='general', canal='push', contenu='Bob')

    def test_compteur_de_non_lues_sur_la_route_du_front(self):
        r = api_as(self.alice).get('/api/notifications/unread-count/')
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.data['count'], 2)

    def test_marquer_une_notification_comme_lue(self):
        api = api_as(self.alice)
        r = api.patch(f'/api/notifications/{self.n1.id}/', {'lu_le': '2020-01-01T00:00:00Z'}, format='json')
        self.assertEqual(r.status_code, 200, r.data)
        self.n1.refresh_from_db()
        self.assertIsNotNone(self.n1.lu_le)
        self.assertGreater(self.n1.lu_le.year, 2020)                    # l'horodatage vient du serveur, pas du client
        self.assertEqual(api.get('/api/notifications/unread-count/').data['count'], 1)

    def test_on_ne_marque_pas_la_notification_d_un_autre(self):
        r = api_as(self.alice).patch(f'/api/notifications/{self.n_bob.id}/', {}, format='json')
        self.assertEqual(r.status_code, 404)
        self.n_bob.refresh_from_db()
        self.assertIsNone(self.n_bob.lu_le)

    def test_enregistrement_du_jeton_push_sur_la_route_du_front(self):
        r = api_as(self.alice).post('/api/notifications/push-token/', {'token': 'tok-123'}, format='json')
        self.assertEqual(r.status_code, 200, r.data)
        self.assertEqual(FCMToken.objects.get(token='tok-123').utilisateur_id, self.alice.pk)

    def test_un_jeton_change_de_proprietaire_quand_un_autre_se_connecte_sur_le_meme_appareil(self):
        api_as(self.alice).post('/api/notifications/push-token/', {'token': 'tok-xyz'}, format='json')
        api_as(self.bob).post('/api/notifications/push-token/', {'token': 'tok-xyz'}, format='json')
        self.assertEqual(FCMToken.objects.get(token='tok-xyz').utilisateur_id, self.bob.pk)

    def test_anciennes_routes_toujours_disponibles(self):
        self.assertEqual(api_as(self.alice).get('/api/unread-count/').status_code, 200)
        self.assertEqual(api_as(self.alice).post('/api/push-token/', {'token': 'old'}, format='json').status_code, 200)


class EnvoiPushTests(TestCase):
    def setUp(self):
        self.alice = Client.objects.create(nom='Alice', telephone='+237677300010', code_client='CL-NT10')
        self.colis = Colis.objects.create(client=self.alice, code_suivi='FM-NT000001', mode_transport='maritime')

    def test_notify_cree_la_notification_et_programme_le_push_apres_validation(self):
        with patch('notifications.tasks.envoyer_push_task.delay') as delay:
            with self.captureOnCommitCallbacks(execute=True):
                n = notify(self.alice, "Votre colis est arrivé", Notification.Type.STATUT_COLIS, colis=self.colis)
        self.assertTrue(Notification.objects.filter(pk=n.pk).exists())
        delay.assert_called_once_with(self.alice.pk, "Mise à jour de votre colis", "Votre colis est arrivé",
                                      f"/shipments/{self.colis.pk}")

    def test_une_panne_du_broker_ne_fait_pas_echouer_l_action_metier(self):
        with patch('notifications.tasks.envoyer_push_task.delay', side_effect=ConnectionError('redis down')):
            with self.captureOnCommitCallbacks(execute=True):
                n = notify(self.alice, "Message", Notification.Type.GENERAL)      # ne lève rien
        self.assertTrue(Notification.objects.filter(pk=n.pk).exists())

    @override_settings(DISABLE_EXTERNAL_MESSAGING=False)
    def test_la_tache_envoie_via_firebase(self):
        with patch('notifications.tasks.send_push_notification') as envoi:
            envoyer_push_task(self.alice.pk, 'Titre', 'Corps', '/x')
        envoi.assert_called_once()

    @override_settings(DISABLE_EXTERNAL_MESSAGING=False)
    def test_une_erreur_firebase_est_absorbee(self):
        with patch('notifications.tasks.send_push_notification', side_effect=RuntimeError('Firebase absent')):
            envoyer_push_task(self.alice.pk, 'Titre', 'Corps')              # ne lève rien

    def test_aucun_envoi_pendant_les_tests(self):
        with patch('notifications.tasks.send_push_notification') as envoi:
            envoyer_push_task(self.alice.pk, 'Titre', 'Corps')
        envoi.assert_not_called()
