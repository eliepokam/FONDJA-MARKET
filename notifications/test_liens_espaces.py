"""Chaque destinataire reçoit le lien de SON espace : un agent ou un administrateur n'est plus renvoyé vers l'interface client."""
from unittest.mock import patch

from django.test import TestCase

from complaints.models import Reclamation
from notifications.models import Notification
from notifications.services import lien_pour, notify, notify_staff
from shipments.models import Colis
from users.models import Administrateur, Agent, Client


class LiensParEspaceTests(TestCase):
    def setUp(self):
        self.client_u = Client.objects.create(nom='Awa', telephone='+237677990001', code_client='CL-LE01')
        self.admin = Administrateur.objects.create(nom='Directrice', telephone='+237677990002')
        self.agent = Agent.objects.create(nom='Agent', telephone='+237677990003', localisation='chine', cree_par=self.admin)
        self.colis = Colis.objects.create(client=self.client_u, code_suivi='FM-LE000001', mode_transport='maritime')

    def test_colis(self):
        self.assertEqual(lien_pour(self.client_u, colis=self.colis), f"/shipments/{self.colis.pk}")
        self.assertEqual(lien_pour(self.agent, colis=self.colis), f"/agent/shipments/{self.colis.pk}")
        self.assertEqual(lien_pour(self.admin, colis=self.colis), f"/admin/colis/{self.colis.pk}")

    def test_reclamation_prioritaire_sur_le_colis(self):
        r = Reclamation.objects.create(client=self.client_u, colis=self.colis, type='retard', description='Où est mon colis ?')
        self.assertEqual(lien_pour(self.client_u, colis=self.colis, reclamation=r), f"/complaints/{r.pk}")
        self.assertEqual(lien_pour(self.agent, colis=self.colis, reclamation=r), f"/agent/complaints/{r.pk}")
        self.assertEqual(lien_pour(self.admin, colis=self.colis, reclamation=r), f"/admin/complaints/{r.pk}")

    def test_pages_et_defaut(self):
        self.assertEqual(lien_pour(self.agent, page='virements'), "/agent/virements")
        self.assertEqual(lien_pour(self.admin, page='virements'), "/admin/virements")
        self.assertEqual(lien_pour(self.admin, page='comptes'), "/admin/users")
        self.assertEqual(lien_pour(self.agent), "/agent/notifications")
        self.assertEqual(lien_pour(self.client_u), "/notifications")

    def test_notify_staff_enregistre_et_pousse_le_lien_de_chaque_espace(self):
        with patch('notifications.tasks.envoyer_push_task.apply') as push:
            with self.captureOnCommitCallbacks(execute=True):
                notify_staff("Preuve de virement à vérifier", Notification.Type.PAIEMENT, colis=self.colis, page='virements')
        liens = dict(Notification.objects.values_list('utilisateur_id', 'lien'))
        self.assertEqual(liens, {self.agent.pk: "/agent/virements", self.admin.pk: "/admin/virements"})
        self.assertEqual(sorted(c.kwargs['args'][3] for c in push.call_args_list), ["/admin/virements", "/agent/virements"])

    def test_le_lien_est_renvoye_par_l_api(self):
        notify(self.agent, "Colis reçu", Notification.Type.STATUT_COLIS, colis=self.colis)
        from rest_framework.test import APIClient
        api = APIClient(); api.force_authenticate(self.agent)
        self.assertEqual(api.get('/api/notifications/').data['results'][0]['lien'], f"/agent/shipments/{self.colis.pk}")


class TacheNonBloquanteTests(TestCase):
    """L'action (ex. valider un virement) répond même si l'envoi du push échoue : plus de fenêtre bloquée sur « Validation... »."""

    def setUp(self):
        self.client_u = Client.objects.create(nom='Awa', telephone='+237677990011', code_client='CL-LE11')

    def test_une_erreur_d_envoi_ne_remonte_pas(self):
        with patch('notifications.tasks.send_push_notification', side_effect=RuntimeError('Firebase absent')):
            with self.captureOnCommitCallbacks(execute=True):
                n = notify(self.client_u, "Bonjour", Notification.Type.GENERAL)
        self.assertTrue(Notification.objects.filter(pk=n.pk).exists())
