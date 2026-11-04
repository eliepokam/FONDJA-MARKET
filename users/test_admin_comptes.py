"""Espace ADMINISTRATEUR : gestion des comptes."""
from django.core.cache import cache
from django.db import connection
from django.test import override_settings
from django.test.utils import CaptureQueriesContext
from django.utils import timezone
from rest_framework.test import APITestCase, APIClient

from core.models import JournalActivite
from notifications.models import Notification
from shipments.models import Colis
from users.models import Administrateur, Agent, Client

MDP = 'Zebre!Orange2026'


def api_as(user):
    api = APIClient()
    api.force_authenticate(user=user)
    return api


def avec_mdp(user):
    user.set_password(MDP)
    user.save()
    return user


class BaseAdmin(APITestCase):
    def setUp(self):
        cache.clear()
        self.admin = avec_mdp(Administrateur.objects.create(nom='Admin Un', telephone='+237677700001'))
        self.agent = avec_mdp(Agent.objects.create(nom='Agent Chine', telephone='+237677700002', localisation='chine', cree_par=self.admin))
        self.api = api_as(self.admin)


class ListeDesComptesTests(BaseAdmin):
    def lister(self, role, **params):
        return self.api.get('/api/users/', {'role': role, **params})

    def test_la_recherche_se_fait_cote_serveur_sur_tous_les_comptes(self):
        for i in range(25):
            Client.objects.create(nom=f'Client {i:02d}', telephone=f'+2376777100{i:02d}', code_client=f'CL-LS{i:02d}')
        Client.objects.create(nom='Zoé Introuvable', telephone='+237677719999', code_client='CL-ZOE')
        tous = self.lister('client').data
        self.assertEqual(tous['count'], 26)
        self.assertEqual(len(tous['results']), 20)                                # pagination : 20 par page
        par_nom = self.lister('client', search='Zoé').data
        self.assertEqual([c['nom'] for c in par_nom['results']], ['Zoé Introuvable'])   # trouvé même hors de la 1re page
        par_tel = self.lister('client', search='677719999').data
        self.assertEqual(par_tel['count'], 1)

    def test_le_compteur_de_colis_actifs_exclut_les_livres(self):
        c = Client.objects.create(nom='Alice', telephone='+237677700010', code_client='CL-AD01')
        Colis.objects.create(client=c, code_suivi='FM-AD000001', mode_transport='maritime')
        Colis.objects.create(client=c, code_suivi='FM-AD000002', mode_transport='maritime', statut='embarque')
        Colis.objects.create(client=c, code_suivi='FM-AD000003', mode_transport='maritime', statut='livre')
        r = self.lister('client').data['results'][0]
        self.assertEqual(r['colis_actifs_count'], 2)

    def test_pas_de_requete_par_client(self):
        self.lister('client')          # échauffement : les requêtes uniques (caches) ne doivent pas fausser la mesure

        def nombre(n):
            Colis.objects.all().delete()
            Client.objects.all().delete()
            for i in range(n):
                c = Client.objects.create(nom=f'C{i}', telephone=f'+2376777200{i:02d}', code_client=f'CL-NQ{i:02d}')
                Colis.objects.create(client=c, code_suivi=f'FM-NQ{n}{i:04d}', mode_transport='maritime')
            with CaptureQueriesContext(connection) as ctx:
                self.lister('client')
            return len(ctx)
        self.assertEqual(nombre(2), nombre(12))

    def test_les_agents_indiquent_entrepot_et_demande_en_attente(self):
        en_attente = Agent.objects.create(nom='Nouvel Agent', telephone='+237677700020', localisation='cameroun',
                                          cree_par=None, statut='desactive')
        suspendu = Agent.objects.create(nom='Agent Suspendu', telephone='+237677700021', localisation='chine',
                                        cree_par=self.admin, statut='desactive')
        lignes = {a['nom']: a for a in self.lister('agent').data['results']}
        self.assertTrue(lignes['Nouvel Agent']['en_attente'])
        self.assertFalse(lignes['Agent Suspendu']['en_attente'])
        self.assertFalse(lignes['Agent Chine']['en_attente'])
        self.assertEqual(lignes['Nouvel Agent']['localisation'], 'cameroun')

    def test_un_agent_supprime_n_apparait_plus(self):
        Agent.objects.filter(pk=self.agent.pk).update(supprime_le=timezone.now(), supprime_par=self.admin, statut='desactive')
        self.assertEqual(self.lister('agent').data['count'], 0)

    def test_reserve_aux_administrateurs(self):
        self.assertEqual(api_as(self.agent).get('/api/users/?role=client').status_code, 403)


class SuspensionEtSuppressionTests(BaseAdmin):
    def basculer(self, cible, statut):
        return self.api.post(f'/api/admin/users/{cible.pk}/statut/', {'statut': statut}, format='json')

    def test_on_ne_suspend_pas_son_propre_compte(self):
        r = self.basculer(self.admin, 'desactive')
        self.assertEqual(r.status_code, 400)
        self.assertIn('propre compte', r.data['detail'])
        self.admin.refresh_from_db()
        self.assertEqual(self.admin.statut, 'actif')

    def test_on_ne_supprime_pas_son_propre_compte(self):
        r = self.api.delete(f'/api/admin/users/{self.admin.pk}/')
        self.assertEqual(r.status_code, 400)
        self.assertTrue(Administrateur.objects.filter(pk=self.admin.pk).exists())

    def test_un_administrateur_peut_en_suspendre_un_autre(self):
        autre = avec_mdp(Administrateur.objects.create(nom='Admin Deux', telephone='+237677700003'))
        self.assertEqual(self.basculer(autre, 'desactive').status_code, 200)

    def test_la_suspension_coupe_immediatement_les_sessions_ouvertes(self):
        session = APIClient()
        r = session.post('/api/auth/login/', {'telephone': '+237677700002', 'password': MDP, 'portail': 'staff'}, format='json')
        self.assertEqual(r.status_code, 200, r.data)
        self.assertEqual(session.get('/api/auth/me/').status_code, 200)

        self.assertEqual(self.basculer(self.agent, 'desactive').status_code, 200)

        self.assertEqual(session.get('/api/auth/me/').status_code, 401)                 # le jeton d'accès ne marche plus
        self.assertEqual(session.post('/api/auth/login/refresh/').status_code, 401)     # ni le renouvellement
        again = APIClient().post('/api/auth/login/', {'telephone': '+237677700002', 'password': MDP, 'portail': 'staff'}, format='json')
        self.assertEqual(again.status_code, 403)

    def test_reactivation_et_journal(self):
        self.basculer(self.agent, 'desactive')
        self.assertEqual(self.basculer(self.agent, 'actif').status_code, 200)
        actions = list(JournalActivite.objects.values_list('action', flat=True))
        self.assertEqual(actions, ['reactivation_compte', 'suspension_compte'])        # plus récent d'abord

    def test_un_agent_supprime_ne_peut_pas_etre_reactive(self):
        self.api.delete(f'/api/admin/users/{self.agent.pk}/')
        r = self.basculer(self.agent, 'actif')
        self.assertEqual(r.status_code, 400)
        self.assertIn('supprimé', r.data['detail'])

    def test_supprimer_un_agent_le_desactive_et_coupe_sa_session(self):
        session = APIClient()
        session.post('/api/auth/login/', {'telephone': '+237677700002', 'password': MDP, 'portail': 'staff'}, format='json')
        r = self.api.delete(f'/api/admin/users/{self.agent.pk}/')
        self.assertEqual(r.status_code, 200)
        self.agent.refresh_from_db()
        self.assertIsNotNone(self.agent.supprime_le)
        self.assertEqual(session.post('/api/auth/login/refresh/').status_code, 401)

    def test_supprimer_un_client_avec_colis_en_cours_est_refuse_clairement(self):
        c = Client.objects.create(nom='Alice', telephone='+237677700030', code_client='CL-AD30')
        Colis.objects.create(client=c, code_suivi='FM-AD300001', mode_transport='maritime')           # encore « déclaré »
        r = self.api.delete(f'/api/admin/users/{c.pk}/')
        self.assertEqual(r.status_code, 400)
        self.assertIn('1 colis en cours', r.data['detail'])
        self.assertTrue(Client.objects.filter(pk=c.pk, nom='Alice').exists())                         # rien n'a été effacé

    def test_supprimer_un_client_sans_donnees(self):
        c = Client.objects.create(nom='Alice', telephone='+237677700031', code_client='CL-AD31')
        self.assertEqual(self.api.delete(f'/api/admin/users/{c.pk}/').status_code, 200)
        self.assertFalse(Client.objects.filter(pk=c.pk).exists())


class InscriptionsAgentsTests(BaseAdmin):
    def inscrire(self):
        return APIClient().post('/api/auth/staff-register/', {'nom': 'Futur Agent', 'telephone': '+237677700040',
                                                              'localisation': 'cameroun', 'password': MDP}, format='json')

    def test_le_message_de_connexion_dit_que_l_inscription_est_en_attente(self):
        self.assertEqual(self.inscrire().status_code, 201)
        r = APIClient().post('/api/auth/login/', {'telephone': '+237677700040', 'password': MDP, 'portail': 'staff'}, format='json')
        self.assertEqual(r.status_code, 403)
        self.assertIn('en attente', r.data['detail'])
        self.assertNotIn('suspendu', r.data['detail'])

    def test_activer_une_demande_trace_le_createur_et_ouvre_l_acces(self):
        self.inscrire()
        agent = Agent.objects.get(telephone='+237677700040')
        self.assertIsNone(agent.cree_par_id)
        self.assertTrue(Notification.objects.filter(utilisateur=self.admin, contenu__contains='Futur Agent').exists())

        r = api_as(self.admin).post(f'/api/admin/users/{agent.pk}/statut/', {'statut': 'actif'}, format='json')
        self.assertEqual(r.status_code, 200)
        agent.refresh_from_db()
        self.assertEqual(agent.cree_par_id, self.admin.pk)
        self.assertTrue(Notification.objects.filter(utilisateur=agent, contenu__contains='activé').exists())
        entree = JournalActivite.objects.get(action='reactivation_compte')
        self.assertEqual(entree.details.get('demande_inscription'), 'acceptée')
        ok = APIClient().post('/api/auth/login/', {'telephone': '+237677700040', 'password': MDP, 'portail': 'staff'}, format='json')
        self.assertEqual(ok.status_code, 200)

    def test_mot_de_passe_trivial_refuse_a_l_inscription_et_a_la_creation(self):
        r = APIClient().post('/api/auth/staff-register/', {'nom': 'X Y', 'telephone': '+237677700041',
                                                           'localisation': 'chine', 'password': '12345678'}, format='json')
        self.assertEqual(r.status_code, 400)
        r = self.api.post('/api/admin/staff-accounts/', {'role': 'agent', 'nom': 'X Y', 'telephone': '+237677700042',
                                                         'localisation': 'chine', 'password': '12345678'}, format='json')
        self.assertEqual(r.status_code, 400)
        self.assertIn('password', r.data)

    def test_creation_d_un_agent_exige_son_entrepot(self):
        r = self.api.post('/api/admin/staff-accounts/', {'role': 'agent', 'nom': 'X Y', 'telephone': '+237677700043',
                                                         'password': MDP}, format='json')
        self.assertEqual(r.status_code, 400)
        self.assertIn('localisation', r.data)

    def test_creation_d_un_agent_valide(self):
        r = self.api.post('/api/admin/staff-accounts/', {'role': 'agent', 'nom': 'Agent Neuf', 'telephone': '+237677700044',
                                                         'localisation': 'chine', 'password': MDP}, format='json')
        self.assertEqual(r.status_code, 201, r.data)
        self.assertEqual(Agent.objects.get(telephone='+237677700044').cree_par_id, self.admin.pk)
        self.assertNotIn('password', r.data)
