"""Discussion d'une réclamation : chaque message indique QUI écrit (nom) et en quelle qualité (rôle), pour l'afficher du bon côté."""
from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.utils import timezone
from rest_framework.test import APITestCase, APIClient

from complaints.models import MessageReclamation, Reclamation
from shipments.models import Colis
from users.models import Administrateur, Agent, Client


def api(user):
    a = APIClient(); a.force_authenticate(user=user); return a


class RolesDesMessagesTests(APITestCase):
    def setUp(self):
        self.client_a = Client.objects.create(nom='Awa Ngo', telephone='+237677998001', code_client='CL-MR01', telephone_verifie_le=timezone.now())
        self.admin = Administrateur.objects.create(nom='Directrice Fondja', telephone='+237677998002')
        self.agent = Agent.objects.create(nom='Michael Agent', telephone='+237677998003', localisation='chine', cree_par=self.admin)
        colis = Colis.objects.create(client=self.client_a, code_suivi='FM-MR000001', mode_transport='maritime')
        self.rec = Reclamation.objects.create(client=self.client_a, colis=colis, type='retard', description='Mon colis est en retard')

    def ecrire(self, expediteur, texte):
        return MessageReclamation.objects.create(reclamation=self.rec, expediteur=expediteur, message=texte)

    def lire(self, user):
        r = api(user).get(f'/api/reclamations/{self.rec.pk}/')
        self.assertEqual(r.status_code, 200, r.data)
        return r.json()['messages']

    def test_chaque_message_porte_le_nom_et_le_role_de_son_auteur(self):
        self.ecrire(self.client_a, 'Bonjour, où est mon colis ?')
        self.ecrire(self.agent, 'Il est en cours de dédouanement.')
        self.ecrire(self.admin, 'Nous vous tenons informé.')
        messages = self.lire(self.client_a)
        self.assertEqual([(m['sender_name'], m['sender_role']) for m in messages],
                         [('Awa Ngo', 'client'), ('Michael Agent', 'agent'), ('Directrice Fondja', 'administrateur')])

    def test_le_meme_contenu_pour_le_client_et_pour_le_personnel(self):
        self.ecrire(self.client_a, 'A'); self.ecrire(self.agent, 'B')
        du_client, de_l_admin, de_l_agent = self.lire(self.client_a), self.lire(self.admin), self.lire(self.agent)
        self.assertEqual([m['sender_role'] for m in du_client], ['client', 'agent'])
        self.assertEqual(du_client, de_l_admin)
        self.assertEqual(du_client, de_l_agent)

    def test_ordre_chronologique_conserve(self):
        for i in range(4):
            self.ecrire(self.client_a if i % 2 == 0 else self.agent, f'm{i}')
        self.assertEqual([m['message'] for m in self.lire(self.admin)], ['m0', 'm1', 'm2', 'm3'])

    def test_un_expediteur_supprime_reste_affichable(self):
        from users.account_deletion import anonymiser_client
        self.ecrire(self.client_a, 'avant suppression')
        Reclamation.objects.filter(pk=self.rec.pk).update(statut='resolue')
        Colis.objects.filter(pk=self.rec.colis_id).update(statut='livre')
        anonymiser_client(self.client_a)
        m = self.lire(self.admin)[0]
        self.assertEqual((m['sender_name'], m['sender_role']), ('Compte supprimé', 'client'))

    def test_le_nombre_de_requetes_ne_depend_pas_du_nombre_de_messages(self):
        self.ecrire(self.client_a, 'un'); self.ecrire(self.agent, 'deux')
        self.lire(self.admin)                                   # échauffement : le tout premier appel inclut la requête d'authentification
        with CaptureQueriesContext(connection) as peu:
            self.lire(self.admin)
        for i in range(10):
            self.ecrire([self.client_a, self.agent, self.admin][i % 3], f'suite {i}')
        with CaptureQueriesContext(connection) as beaucoup:
            self.lire(self.admin)
        self.assertEqual(len(peu), len(beaucoup), "un message de plus ne doit pas ajouter de requête (N+1)")

    def test_la_liste_des_reclamations_reste_correcte(self):
        self.ecrire(self.agent, 'réponse')
        r = api(self.client_a).get('/api/reclamations/')
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json()['results'][0]['messages'][0]['sender_role'], 'agent')
