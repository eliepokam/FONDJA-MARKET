"""Espace ADMINISTRATEUR : grille tarifaire et lots de dédouanement."""
from rest_framework.test import APITestCase, APIClient

from core.models import JournalActivite
from shipments.models import Colis, LotDedouanement, Tarif
from users.models import Administrateur, Agent, Client


def api_as(user):
    api = APIClient()
    api.force_authenticate(user=user)
    return api


class BaseTarif(APITestCase):
    def setUp(self):
        self.admin = Administrateur.objects.create(nom='Admin', telephone='+237677900001')
        self.agent = Agent.objects.create(nom='Agent', telephone='+237677900002', localisation='chine', cree_par=self.admin)
        self.client_user = Client.objects.create(nom='Alice', telephone='+237677900003', code_client='CL-TA01')
        self.api = api_as(self.admin)

    def creer(self, mode='maritime', **extra):
        data = {'mode_transport': mode, 'delai_min_jours': 30, 'delai_max_jours': 45, 'actif': True}
        data.update({'prix_par_m3': 250000} if mode == 'maritime' else {'prix_par_kg': 12000})
        data.update(extra)
        return self.api.post('/api/tarifs/', data, format='json')


class UnTarifActifParModeTests(BaseTarif):
    def test_creer_un_tarif_actif_desactive_l_ancien_du_meme_mode_seulement(self):
        premier = self.creer('maritime').data['id']
        aerien = self.creer('aerien_standard').data['id']
        second = self.creer('maritime', prix_par_m3=300000).data['id']
        etats = {t.pk: t.actif for t in Tarif.objects.all()}
        self.assertEqual((etats[premier], etats[second], etats[aerien]), (False, True, True))

    def test_creer_un_tarif_inactif_ne_touche_pas_au_tarif_actif(self):
        actif = self.creer('maritime').data['id']
        self.creer('maritime', actif=False, prix_par_m3=1)
        self.assertTrue(Tarif.objects.get(pk=actif).actif)

    def test_reactiver_un_ancien_tarif_desactive_l_actuel(self):
        ancien = self.creer('maritime').data['id']
        actuel = self.creer('maritime', prix_par_m3=300000).data['id']
        r = self.api.patch(f'/api/tarifs/{ancien}/', {'actif': True}, format='json')
        self.assertEqual(r.status_code, 200)
        self.assertEqual((Tarif.objects.get(pk=ancien).actif, Tarif.objects.get(pk=actuel).actif), (True, False))

    def test_le_cout_utilise_bien_le_tarif_actif(self):
        from decimal import Decimal
        from shipments.pricing import calculer_cout
        self.creer('maritime', prix_par_m3=100)
        self.creer('maritime', prix_par_m3=300)
        c = Colis.objects.create(client=self.client_user, code_suivi='FM-TA000001', mode_transport='maritime', volume_m3=Decimal('2'))
        self.assertEqual(calculer_cout(c), Decimal('600'))


class ValidationDesTarifsTests(BaseTarif):
    def test_un_tarif_maritime_exige_un_prix_au_m3(self):
        r = self.api.post('/api/tarifs/', {'mode_transport': 'maritime', 'prix_par_kg': 500}, format='json')
        self.assertEqual(r.status_code, 400)
        self.assertIn('prix_par_m3', r.data)
        self.assertEqual(self.api.post('/api/tarifs/', {'mode_transport': 'maritime'}, format='json').status_code, 400)

    def test_un_tarif_aerien_exige_un_prix_au_kg(self):
        for mode in ('aerien_express', 'aerien_standard'):
            r = self.api.post('/api/tarifs/', {'mode_transport': mode, 'prix_par_m3': 5}, format='json')
            self.assertEqual(r.status_code, 400, mode)
            self.assertIn('prix_par_kg', r.data)

    def test_prix_nul_ou_negatif_refuse(self):
        self.assertEqual(self.creer('maritime', prix_par_m3=0).status_code, 400)
        self.assertEqual(self.creer('aerien_standard', prix_par_kg=-5).status_code, 400)

    def test_delais_incoherents_refuses_a_la_creation_et_a_la_modification(self):
        self.assertEqual(self.creer('maritime', delai_min_jours=50, delai_max_jours=40).status_code, 400)
        tid = self.creer('maritime').data['id']
        self.assertEqual(self.api.patch(f'/api/tarifs/{tid}/', {'delai_min_jours': 99}, format='json').status_code, 400)

    def test_modifier_seulement_les_delais_fonctionne(self):
        tid = self.creer('maritime').data['id']
        r = self.api.patch(f'/api/tarifs/{tid}/', {'delai_min_jours': 20, 'delai_max_jours': 35}, format='json')
        self.assertEqual(r.status_code, 200, r.data)


class DroitsEtJournalTarifsTests(BaseTarif):
    def test_seul_un_administrateur_ecrit_les_autres_lisent(self):
        data = {'mode_transport': 'maritime', 'prix_par_m3': 1}
        self.assertEqual(api_as(self.agent).post('/api/tarifs/', data, format='json').status_code, 403)
        self.assertEqual(api_as(self.client_user).post('/api/tarifs/', data, format='json').status_code, 403)
        self.creer('maritime')
        self.assertEqual(api_as(self.agent).get('/api/tarifs/').status_code, 200)

    def test_un_client_ne_voit_que_les_tarifs_actifs(self):
        self.creer('maritime')
        self.creer('maritime', prix_par_m3=300000)                          # le premier devient inactif
        ids = [t['actif'] for t in api_as(self.client_user).get('/api/tarifs/').data['results']]
        self.assertEqual(ids, [True])

    def test_le_journal_dit_ce_qui_a_vraiment_change(self):
        tid = self.creer('aerien_standard').data['id']
        self.api.patch(f'/api/tarifs/{tid}/', {'actif': False}, format='json')
        entree = JournalActivite.objects.filter(action='modification_tarif').first()
        self.assertEqual(entree.details, {'actif': 'True → False'})            # avant : « ancien prix = nouveau prix », sans rien d'utile
        self.api.patch(f'/api/tarifs/{tid}/', {'prix_par_kg': 15000}, format='json')
        entree = JournalActivite.objects.filter(action='modification_tarif').first()
        self.assertIn('prix_par_kg', entree.details)
        self.assertNotIn('actif', entree.details)

    def test_creation_et_suppression_sont_journalisees(self):
        tid = self.creer('maritime').data['id']
        self.api.delete(f'/api/tarifs/{tid}/')
        self.assertEqual(list(JournalActivite.objects.values_list('action', flat=True)), ['suppression_tarif', 'creation_tarif'])


class LotsAdminTests(BaseTarif):
    def test_creation_et_suppression_de_lot_journalisees_et_colis_detaches(self):
        r = self.api.post('/api/lots-dedouanement/', {'numero_besc': 'BESC-ADM-1'}, format='json')
        self.assertEqual(r.status_code, 201, r.data)
        lot_id = r.data['id']
        c = Colis.objects.create(client=self.client_user, code_suivi='FM-LT000001', mode_transport='maritime', lot_id=lot_id)
        self.assertEqual(self.api.get('/api/lots-dedouanement/').data['results'][0]['colis_count'], 1)

        self.assertEqual(self.api.delete(f'/api/lots-dedouanement/{lot_id}/').status_code, 204)
        c.refresh_from_db()
        self.assertIsNone(c.lot_id)                                               # le colis n'est pas supprimé, il repasse « sans lot »
        entrees = list(JournalActivite.objects.order_by('cree_le').values_list('action', 'cible_description'))
        self.assertEqual(entrees[0], ('creation_lot', 'BESC-ADM-1'))
        self.assertEqual(entrees[1][0], 'suppression_lot')
        self.assertIn('1 colis', entrees[1][1])

    def test_un_agent_lit_les_lots_mais_ne_les_modifie_pas(self):
        lot = LotDedouanement.objects.create(numero_besc='BESC-9', administrateur=self.admin)
        self.assertEqual(api_as(self.agent).get('/api/lots-dedouanement/').status_code, 200)
        self.assertEqual(api_as(self.agent).post('/api/lots-dedouanement/', {'numero_besc': 'X'}, format='json').status_code, 403)
        self.assertEqual(api_as(self.agent).delete(f'/api/lots-dedouanement/{lot.pk}/').status_code, 403)

    def test_numero_besc_en_double_refuse_avec_un_message(self):
        self.api.post('/api/lots-dedouanement/', {'numero_besc': 'BESC-DUP'}, format='json')
        for doublon in ('BESC-DUP', ' besc-dup '):                            # même numéro, casse et espaces ignorés
            r = self.api.post('/api/lots-dedouanement/', {'numero_besc': doublon}, format='json')
            self.assertEqual(r.status_code, 400, doublon)
            self.assertIn('numero_besc', r.data)
        self.assertEqual(LotDedouanement.objects.count(), 1)

    def test_numero_besc_vide_refuse_et_renommage_possible(self):
        self.assertEqual(self.api.post('/api/lots-dedouanement/', {'numero_besc': '   '}, format='json').status_code, 400)
        lot_id = self.api.post('/api/lots-dedouanement/', {'numero_besc': 'BESC-A'}, format='json').data['id']
        r = self.api.patch(f'/api/lots-dedouanement/{lot_id}/', {'numero_besc': 'BESC-A'}, format='json')   # son propre numéro : pas un doublon
        self.assertEqual(r.status_code, 200, r.data)
