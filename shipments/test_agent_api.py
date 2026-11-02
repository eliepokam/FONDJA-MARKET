"""Espace AGENT : changements de statut, actions groupées, recherche, incidents, statistiques."""
from datetime import timedelta
from decimal import Decimal

from django.utils import timezone
from rest_framework.test import APITestCase, APIClient

from notifications.models import Notification
from shipments.models import Colis, Incident, LotDedouanement, Tarif
from tracking.models import HistoriqueStatutColis
from users.models import Administrateur, Agent, Client


def api_as(user):
    api = APIClient()
    api.force_authenticate(user=user)
    return api


class BaseAgent(APITestCase):
    def setUp(self):
        self.alice = Client.objects.create(nom='Alice Ngo', telephone='+237677500001', code_client='CL-00001')
        self.bob = Client.objects.create(nom='Bob Mbarga', telephone='+237677500002', code_client='CL-00002')
        self.admin = Administrateur.objects.create(nom='Admin', telephone='+237677500003')
        self.agent = Agent.objects.create(nom='Agent Chine', telephone='+237677500004', localisation='chine', cree_par=self.admin)
        self.agent_cm = Agent.objects.create(nom='Agent Cameroun', telephone='+237677500005', localisation='cameroun', cree_par=self.admin)
        self.c1 = Colis.objects.create(client=self.alice, code_suivi='FM-AG000001', mode_transport='maritime')
        self.c2 = Colis.objects.create(client=self.bob, code_suivi='FM-AG000002', mode_transport='aerien_standard')

    def statut(self, user, colis, statut, **extra):
        return api_as(user).post(f'/api/colis/{colis.code_suivi}/statut/', {'statut': statut, **extra}, format='json')


class ChangementDeStatutTests(BaseAgent):
    def test_avancer_cree_historique_et_notification(self):
        r = self.statut(self.agent, self.c1, 'recu_entrepot', note='Reçu OK')
        self.assertEqual(r.status_code, 200, r.data)
        self.c1.refresh_from_db()
        self.assertEqual(self.c1.statut, 'recu_entrepot')
        h = HistoriqueStatutColis.objects.get(colis=self.c1)
        self.assertEqual((h.statut, h.note, h.agent_id), ('recu_entrepot', 'Reçu OK', self.agent.pk))
        self.assertTrue(Notification.objects.filter(utilisateur=self.alice, type='statut_colis', colis=self.c1).exists())

    def test_sauter_des_etapes_vers_l_avant_reste_possible(self):
        self.assertEqual(self.statut(self.agent, self.c1, 'en_transit').status_code, 200)          # reçu et embarqué non saisis
        self.assertEqual(self.statut(self.agent_cm, self.c2, 'dedouanement').status_code, 200)

    def test_meme_statut_refuse_et_ne_renotifie_pas(self):
        self.statut(self.agent, self.c1, 'embarque')
        r = self.statut(self.agent, self.c1, 'embarque')
        self.assertEqual(r.status_code, 400)
        self.assertIn('déjà', r.data['detail'])
        self.assertEqual(HistoriqueStatutColis.objects.filter(colis=self.c1).count(), 1)
        self.assertEqual(Notification.objects.filter(colis=self.c1).count(), 1)

    def test_un_agent_ne_fait_pas_reculer_un_colis(self):
        self.statut(self.agent, self.c1, 'en_transit')
        r = self.statut(self.agent, self.c1, 'recu_entrepot')
        self.assertEqual(r.status_code, 400)
        self.c1.refresh_from_db()
        self.assertEqual(self.c1.statut, 'en_transit')

    def test_un_colis_livre_ne_recule_pas_sous_la_main_d_un_agent(self):
        self.assertEqual(self.statut(self.agent_cm, self.c1, 'livre').status_code, 200)
        r = self.statut(self.agent_cm, self.c1, 'en_livraison')                 # étape permise à cet agent, mais en arrière
        self.assertEqual(r.status_code, 400)
        self.assertIn('reculer', r.data['detail'])
        self.assertEqual(self.statut(self.agent_cm, self.c1, 'declaree').status_code, 400)
        self.c1.refresh_from_db()
        self.assertEqual(self.c1.statut, 'livre')

    def test_un_administrateur_peut_corriger_en_arriere(self):
        self.statut(self.agent_cm, self.c1, 'livre')
        self.assertEqual(self.statut(self.admin, self.c1, 'en_livraison').status_code, 200)
        self.c1.refresh_from_db()
        self.assertEqual(self.c1.statut, 'en_livraison')

    def test_statut_invalide_et_note_trop_longue(self):
        self.assertEqual(self.statut(self.agent, self.c1, 'nimporte').status_code, 400)
        self.assertEqual(self.statut(self.agent, self.c1, 'embarque', note='x' * 300).status_code, 400)

    def test_un_client_ne_change_pas_un_statut(self):
        self.assertEqual(self.statut(self.alice, self.c1, 'livre').status_code, 403)

    def test_la_date_estimee_vient_du_tarif_actif_le_plus_recent_a_l_embarquement(self):
        Tarif.objects.create(mode_transport='maritime', prix_par_m3=1, delai_max_jours=99, actif=False)
        Tarif.objects.create(mode_transport='maritime', prix_par_m3=1, delai_max_jours=60)
        Tarif.objects.create(mode_transport='maritime', prix_par_m3=1, delai_max_jours=45)      # le plus récent actif
        self.statut(self.agent, self.c1, 'embarque')
        self.c1.refresh_from_db()
        self.assertEqual(self.c1.date_livraison_estimee, timezone.localdate() + timedelta(days=45))

    def test_une_date_deja_fixee_n_est_pas_ecrasee(self):
        Tarif.objects.create(mode_transport='maritime', prix_par_m3=1, delai_max_jours=45)
        fixe = timezone.localdate() + timedelta(days=10)
        Colis.objects.filter(pk=self.c1.pk).update(date_livraison_estimee=fixe)
        self.statut(self.agent, self.c1, 'embarque')
        self.c1.refresh_from_db()
        self.assertEqual(self.c1.date_livraison_estimee, fixe)

    def test_sans_tarif_le_changement_de_statut_fonctionne_quand_meme(self):
        self.assertEqual(self.statut(self.agent, self.c1, 'embarque').status_code, 200)
        self.c1.refresh_from_db()
        self.assertIsNone(self.c1.date_livraison_estimee)


class ActionsGroupeesTests(BaseAgent):
    def bulk(self, user, **data):
        return api_as(user).post('/api/colis/bulk-change-status/', data, format='json')

    def test_met_a_jour_les_colis_eligibles_et_liste_les_ignores(self):
        self.statut(self.agent, self.c2, 'en_transit')
        r = self.bulk(self.agent, colis_ids=[self.c1.id, self.c2.id], statut='recu_entrepot')   # c2 est déjà plus loin
        self.assertEqual(r.status_code, 200, r.data)
        self.assertEqual(r.data['count'], 1)
        self.assertEqual([i['code_suivi'] for i in r.data['ignores']], ['FM-AG000002'])
        self.c1.refresh_from_db(); self.c2.refresh_from_db()
        self.assertEqual((self.c1.statut, self.c2.statut), ('recu_entrepot', 'en_transit'))

    def test_par_lot(self):
        lot = LotDedouanement.objects.create(numero_besc='BESC-9', administrateur=self.admin)
        Colis.objects.filter(pk__in=[self.c1.pk, self.c2.pk]).update(lot=lot)
        r = self.bulk(self.agent_cm, lot_id=lot.id, statut='dedouanement')       # la douane relève du Cameroun
        self.assertEqual(r.data['count'], 2)

    def test_chaque_client_est_notifie(self):
        self.bulk(self.agent, colis_ids=[self.c1.id, self.c2.id], statut='embarque')
        self.assertTrue(Notification.objects.filter(utilisateur=self.alice, colis=self.c1).exists())
        self.assertTrue(Notification.objects.filter(utilisateur=self.bob, colis=self.c2).exists())

    def test_entrees_invalides_donnent_400_et_non_500(self):
        for corps in ({'colis_ids': ['abc'], 'statut': 'embarque'}, {'colis_ids': 'x', 'statut': 'embarque'},
                      {'colis_ids': [], 'statut': 'embarque'}, {'statut': 'embarque'},
                      {'colis_ids': [self.c1.id], 'statut': 'bizarre'}):
            self.assertEqual(self.bulk(self.agent, **corps).status_code, 400, corps)

    def test_un_client_ne_peut_pas(self):
        self.assertEqual(self.bulk(self.alice, colis_ids=[self.c1.id], statut='livre').status_code, 403)

    def test_assignation_a_un_lot(self):
        lot = LotDedouanement.objects.create(numero_besc='BESC-1', administrateur=self.admin)
        r = api_as(self.agent).post('/api/colis/bulk-assign-lot/', {'colis_ids': [self.c1.id, self.c2.id], 'lot_id': lot.id}, format='json')
        self.assertEqual(r.data['count'], 2)
        self.assertEqual(api_as(self.agent).post('/api/colis/bulk-assign-lot/', {'colis_ids': ['x'], 'lot_id': lot.id}, format='json').status_code, 400)


class RechercheEtLotsTests(BaseAgent):
    def chercher(self, terme):
        return {c['code_suivi'] for c in api_as(self.agent).get(f'/api/colis/?search={terme}').data['results']}

    def test_recherche_par_code_de_suivi_telephone_et_nom(self):
        self.assertEqual(self.chercher('FM-AG000001'), {'FM-AG000001'})
        self.assertEqual(self.chercher('fm-ag000002'), {'FM-AG000002'})       # insensible à la casse (QR scanné ou saisi)
        self.assertEqual(self.chercher('677500001'), {'FM-AG000001'})
        self.assertEqual(self.chercher('Mbarga'), {'FM-AG000002'})
        self.assertEqual(self.chercher('CL-00002'), set())                   # le code client n'est pas un critère de recherche

    def test_le_nombre_de_colis_par_lot_est_exact(self):
        lot = LotDedouanement.objects.create(numero_besc='BESC-5', administrateur=self.admin)
        vide = LotDedouanement.objects.create(numero_besc='BESC-6', administrateur=self.admin)
        Colis.objects.filter(pk__in=[self.c1.pk, self.c2.pk]).update(lot=lot)
        comptes = {l['numero_besc']: l['colis_count'] for l in api_as(self.agent).get('/api/lots-dedouanement/').data['results']}
        self.assertEqual(comptes, {'BESC-5': 2, 'BESC-6': 0})


class IncidentsAgentTests(BaseAgent):
    def creer(self, user=None, **data):
        return api_as(user or self.agent).post('/api/incidents/', {'type': 'retard', 'gravite': 'important',
                                                                  'description': 'Contrôle renforcé', **data}, format='json')

    def test_un_incident_colis_notifie_le_client(self):
        r = self.creer(colis=self.c1.id)
        self.assertEqual(r.status_code, 201, r.data)
        self.assertTrue(Notification.objects.filter(utilisateur=self.alice, type='incident').exists())
        self.assertFalse(Notification.objects.filter(utilisateur=self.bob).exists())

    def test_un_incident_de_lot_notifie_tous_les_clients_concernes(self):
        lot = LotDedouanement.objects.create(numero_besc='BESC-7', administrateur=self.admin)
        Colis.objects.filter(pk__in=[self.c1.pk, self.c2.pk]).update(lot=lot)
        self.assertEqual(self.creer(lot=lot.id).status_code, 201)
        self.assertEqual(Notification.objects.filter(type='incident').count(), 2)

    def test_colis_ou_lot_mais_pas_les_deux_ni_aucun(self):
        lot = LotDedouanement.objects.create(numero_besc='BESC-8', administrateur=self.admin)
        self.assertEqual(self.creer().status_code, 400)
        self.assertEqual(self.creer(colis=self.c1.id, lot=lot.id).status_code, 400)
        self.assertFalse(Incident.objects.exists())

    def test_resoudre_cloture_et_previent_le_client(self):
        inc_id = self.creer(colis=self.c1.id).data['id']
        Notification.objects.all().delete()
        r = api_as(self.agent).post(f'/api/incidents/{inc_id}/resoudre/')
        self.assertEqual(r.status_code, 200, r.data)
        self.assertIsNotNone(Incident.objects.get(pk=inc_id).resolu_le)
        self.assertTrue(Notification.objects.filter(utilisateur=self.alice, contenu__contains='résolu').exists())
        self.assertEqual(api_as(self.agent).post(f'/api/incidents/{inc_id}/resoudre/').status_code, 409)

    def test_filtre_actif_et_resolu(self):
        a = self.creer(colis=self.c1.id).data['id']
        b = self.creer(colis=self.c2.id).data['id']
        api_as(self.agent).post(f'/api/incidents/{b}/resoudre/')
        actifs = [i['id'] for i in api_as(self.agent).get('/api/incidents/?etat=actif').data['results']]
        resolus = [i['id'] for i in api_as(self.agent).get('/api/incidents/?etat=resolu').data['results']]
        self.assertEqual((actifs, resolus), ([a], [b]))

    def test_un_client_ne_cree_ni_ne_resout(self):
        self.assertEqual(self.creer(user=self.alice, colis=self.c1.id).status_code, 403)
        inc_id = self.creer(colis=self.c1.id).data['id']
        self.assertEqual(api_as(self.alice).post(f'/api/incidents/{inc_id}/resoudre/').status_code, 403)


class StatistiquesEtProfilTests(BaseAgent):
    def test_le_tableau_de_bord_recoit_tous_les_statuts(self):
        self.statut(self.agent, self.c2, 'embarque')
        r = api_as(self.agent).get('/api/admin/stats/')
        self.assertEqual(r.status_code, 200)
        etapes = r.data['colis_par_etape']
        self.assertEqual(set(etapes), {v for v, _ in Colis.Statut.choices})
        self.assertEqual((etapes['declaree'], etapes['embarque'], etapes['livre']), (1, 1, 0))     # « À réceptionner » n'est plus bloqué à 0

    def test_un_client_n_accede_pas_aux_statistiques(self):
        self.assertEqual(api_as(self.alice).get('/api/admin/stats/').status_code, 403)

    def test_le_profil_agent_indique_son_entrepot(self):
        r = api_as(self.agent).get('/api/auth/me/')
        self.assertEqual((r.data['role'], r.data['localisation']), ('agent', 'chine'))


class EtapesSelonLEntrepotTests(BaseAgent):
    def test_le_profil_communique_les_etapes_autorisees(self):
        chine = api_as(self.agent).get('/api/auth/me/').data['statuts_autorises']
        cameroun = api_as(self.agent_cm).get('/api/auth/me/').data['statuts_autorises']
        tous = api_as(self.admin).get('/api/auth/me/').data['statuts_autorises']
        self.assertEqual(chine, ['recu_entrepot', 'embarque', 'en_transit'])
        self.assertEqual(cameroun, ['en_transit', 'dedouanement', 'arrive_depot_local', 'en_livraison', 'livre'])
        self.assertEqual(tous, [v for v, _ in Colis.Statut.choices])

    def test_un_agent_chine_ne_peut_pas_enregistrer_les_etapes_du_cameroun(self):
        for statut in ('dedouanement', 'arrive_depot_local', 'en_livraison', 'livre'):
            r = self.statut(self.agent, self.c1, statut)
            self.assertEqual(r.status_code, 400, statut)
            self.assertIn('Chine', r.data['detail'])
        self.c1.refresh_from_db()
        self.assertEqual(self.c1.statut, 'declaree')
        self.assertFalse(HistoriqueStatutColis.objects.exists())
        self.assertFalse(Notification.objects.exists())

    def test_un_agent_cameroun_ne_peut_pas_enregistrer_les_etapes_de_chine(self):
        for statut in ('recu_entrepot', 'embarque'):
            r = self.statut(self.agent_cm, self.c1, statut)
            self.assertEqual(r.status_code, 400, statut)
            self.assertIn('Cameroun', r.data['detail'])

    def test_les_deux_sites_peuvent_confirmer_le_transit(self):
        self.assertEqual(self.statut(self.agent, self.c1, 'en_transit').status_code, 200)
        self.assertEqual(self.statut(self.agent_cm, self.c2, 'en_transit').status_code, 200)

    def test_un_administrateur_peut_tout_enregistrer(self):
        for statut in ('recu_entrepot', 'dedouanement', 'livre'):
            self.assertEqual(self.statut(self.admin, self.c1, statut).status_code, 200, statut)

    def test_l_action_groupee_respecte_aussi_la_restriction(self):
        r = api_as(self.agent).post('/api/colis/bulk-change-status/', {'colis_ids': [self.c1.id, self.c2.id], 'statut': 'livre'}, format='json')
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.data['count'], 0)
        self.assertEqual(len(r.data['ignores']), 2)
        self.assertIn('Chine', r.data['ignores'][0]['raison'])
        self.assertFalse(Colis.objects.filter(statut='livre').exists())

    def test_l_action_groupee_fonctionne_pour_les_etapes_du_bon_site(self):
        r = api_as(self.agent).post('/api/colis/bulk-change-status/', {'colis_ids': [self.c1.id, self.c2.id], 'statut': 'embarque'}, format='json')
        self.assertEqual(r.data['count'], 2)
