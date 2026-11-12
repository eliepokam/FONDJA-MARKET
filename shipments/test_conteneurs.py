"""Conteneurs (lots d'embarquement) : remplissage en %, évolution, clôture, et frontière admin / agent."""
import io
import tempfile
from datetime import timedelta
from decimal import Decimal

from django.core.cache import cache
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import override_settings
from django.utils import timezone
from PIL import Image
from rest_framework.test import APITestCase, APIClient

from core.models import JournalActivite
from payments.models import Paiement
from shipments.models import Colis, LotDedouanement, RemplissageLot
from tracking.models import PhotoColis
from users.models import Administrateur, Agent, Client


def api(user):
    a = APIClient(); a.force_authenticate(user=user); return a


class Base(APITestCase):
    def setUp(self):
        cache.clear()
        self.admin = Administrateur.objects.create(nom='Directrice', telephone='+237677992001')
        self.agent = Agent.objects.create(nom='Agent Chine', telephone='+237677992002', localisation='chine', cree_par=self.admin)
        self.agent2 = Agent.objects.create(nom='Agent Chine 2', telephone='+237677992003', localisation='chine', cree_par=self.admin)
        self.client_a = Client.objects.create(nom='Awa', telephone='+237677992004', code_client='CL-CT01', telephone_verifie_le=timezone.now())
        self.n = 0

    def colis(self, volume=None, declare=None, statut='recu_entrepot', lot=None):
        self.n += 1
        return Colis.objects.create(client=self.client_a, code_suivi=f'FM-CT{self.n:06d}', mode_transport='maritime', statut=statut,
                                    volume_m3=volume, volume_declare_m3=declare, lot=lot)

    def lot(self, besc='BESC-1', **kw):
        return LotDedouanement.objects.create(numero_besc=besc, administrateur=self.admin, **kw)

    def lire(self, lot, user=None):
        r = api(user or self.admin).get('/api/lots-dedouanement/')
        self.assertEqual(r.status_code, 200)
        return next((x for x in (r.json()['results'] if 'results' in r.json() else r.json()) if x['id'] == lot.pk), None)


class CreationTests(Base):
    def creer(self, **donnees):
        return api(self.admin).post('/api/lots-dedouanement/', {'numero_besc': 'BESC-NEW', **donnees}, format='json')

    def test_capacites_standard_selon_le_type(self):
        for besc, type_, attendu in (('B20', '20', '33.00'), ('B40', '40', '67.00'), ('B40HC', '40hc', '76.00')):
            r = self.creer(numero_besc=besc, type_conteneur=type_)
            self.assertEqual(r.status_code, 201, r.data)
            self.assertEqual(r.json()['capacite_m3'], attendu)
            self.assertEqual(r.json()['statut'], 'ouvert')
            self.assertEqual(r.json()['pourcentage'], 0.0)

    def test_defaut_vingt_pieds_comme_avant(self):
        r = self.creer()
        self.assertEqual((r.json()['type_conteneur'], r.json()['capacite_m3']), ('20', '33.00'))

    def test_capacite_modifiable_et_type_autre(self):
        self.assertEqual(self.creer(numero_besc='B1', type_conteneur='20', capacite_m3='30').json()['capacite_m3'], '30.00')
        r = self.creer(numero_besc='B2', type_conteneur='autre')
        self.assertEqual(r.status_code, 400)
        self.assertIn('capacite_m3', r.json())
        self.assertEqual(self.creer(numero_besc='B3', type_conteneur='autre', capacite_m3='50').json()['capacite_m3'], '50.00')
        self.assertEqual(self.creer(numero_besc='B4', capacite_m3='0').status_code, 400)

    def test_numero_besc_en_double_refuse(self):
        self.creer(numero_besc='BESC-X')
        self.assertEqual(self.creer(numero_besc='besc-x ').status_code, 400)

    def test_creation_journalisee_et_premier_point_de_la_courbe(self):
        lot_id = self.creer(type_conteneur='40').json()['id']
        self.assertEqual(RemplissageLot.objects.filter(lot_id=lot_id).count(), 1)
        self.assertEqual(RemplissageLot.objects.get(lot_id=lot_id).pourcentage, Decimal('0'))
        entree = JournalActivite.objects.get(action='creation_lot')
        self.assertEqual(entree.details['capacite_m3'], '67.00')

    def test_un_agent_ne_cree_ni_ne_modifie_ni_ne_supprime(self):
        lot = self.lot()
        a = api(self.agent)
        self.assertEqual(a.post('/api/lots-dedouanement/', {'numero_besc': 'X'}, format='json').status_code, 403)
        self.assertEqual(a.patch(f'/api/lots-dedouanement/{lot.pk}/', {'capacite_m3': '10'}, format='json').status_code, 403)
        self.assertEqual(a.delete(f'/api/lots-dedouanement/{lot.pk}/').status_code, 403)
        self.assertEqual(a.post(f'/api/lots-dedouanement/{lot.pk}/cloturer/').status_code, 403)

    def test_modification_de_capacite_recalcule_le_pourcentage_et_journalise(self):
        lot = self.lot(); self.colis(volume=Decimal('16.5'), lot=lot)
        self.assertEqual(self.lire(lot)['pourcentage'], 50.0)
        r = api(self.admin).patch(f'/api/lots-dedouanement/{lot.pk}/', {'type_conteneur': '40'}, format='json')
        self.assertEqual(r.status_code, 200, r.data)
        self.assertEqual(r.json()['capacite_m3'], '67.00')
        self.assertAlmostEqual(self.lire(lot)['pourcentage'], 24.63, places=2)
        self.assertEqual(JournalActivite.objects.filter(action='modification_lot').count(), 1)


class RemplissageEtEvolutionTests(Base):
    def test_volume_mesure_prefere_au_volume_declare(self):
        lot = self.lot()
        self.colis(volume=Decimal('16.5'), declare=Decimal('99'), lot=lot)     # mesuré : 16,5 (le déclaré est ignoré)
        self.colis(declare=Decimal('3.3'), lot=lot)                             # non mesuré : on prend le déclaré
        l = self.lire(lot)
        self.assertEqual((l['volume_m3'], l['pourcentage'], l['colis_count'], l['colis_livres']), (19.8, 60.0, 2, 0))
        self.assertFalse(l['surcharge'])

    def test_surcharge_signalee(self):
        lot = self.lot(); self.colis(volume=Decimal('35'), lot=lot)
        l = self.lire(lot)
        self.assertTrue(l['surcharge'])
        self.assertGreater(l['pourcentage'], 100)

    def test_chaque_changement_ajoute_un_point_a_la_courbe(self):
        lot = self.lot(); RemplissageLot.objects.filter(lot=lot).delete()
        c1, c2 = self.colis(volume=Decimal('3.3')), self.colis(volume=Decimal('6.6'))
        api(self.agent).post('/api/colis/bulk-assign-lot/', {'colis_ids': [c1.pk, c2.pk], 'lot_id': lot.pk}, format='json')
        self.assertEqual([float(p.pourcentage) for p in lot.remplissages.all()], [30.0])        # une seule photographie pour l'ensemble
        Colis.objects.get(pk=c1.pk).__class__.objects.filter(pk=c1.pk)                          # (lecture sans effet)
        c1.refresh_from_db(); c1.volume_m3 = Decimal('9.9'); c1.save()                           # re-mesure par un agent → nouveau point
        self.assertEqual(float(lot.remplissages.last().pourcentage), 50.0)
        c2.refresh_from_db(); c2.statut = 'embarque'; c2.save()                                  # un simple changement de statut : PAS de nouveau point
        self.assertEqual(lot.remplissages.count(), 2)
        c2.delete()                                                                              # colis supprimé → point
        self.assertEqual(float(lot.remplissages.last().pourcentage), 30.0)

    def test_le_deplacement_d_un_colis_photographie_les_deux_conteneurs(self):
        a, b = self.lot('A'), self.lot('B'); c = self.colis(volume=Decimal('6.6'), lot=a)
        RemplissageLot.objects.all().delete()
        c.refresh_from_db(); c.lot = b; c.save()
        self.assertEqual(float(a.remplissages.last().pourcentage), 0.0)
        self.assertEqual(float(b.remplissages.last().pourcentage), 20.0)

    def test_route_evolution_et_variation_sur_sept_jours(self):
        lot = self.lot(); RemplissageLot.objects.filter(lot=lot).delete()
        ancien = RemplissageLot.objects.create(lot=lot, volume_m3=Decimal('3.3'), pourcentage=Decimal('10'), nb_colis=1)
        RemplissageLot.objects.filter(pk=ancien.pk).update(cree_le=timezone.now() - timedelta(days=10))
        self.colis(volume=Decimal('9.9'), lot=lot)                                               # 30 % maintenant
        r = api(self.admin).get(f'/api/lots-dedouanement/{lot.pk}/evolution/').json()
        self.assertEqual(r['variation_7j'], 20.0)                                                # 30 % − 10 % il y a plus de 7 jours
        self.assertEqual([p['pourcentage'] for p in r['points']], [10.0, 30.0])
        self.assertTrue(r['points'][0]['date'] < r['points'][1]['date'])

    def test_conteneur_recent_variation_depuis_zero(self):
        lot = self.lot(); self.colis(volume=Decimal('16.5'), lot=lot)
        self.assertEqual(api(self.admin).get(f'/api/lots-dedouanement/{lot.pk}/evolution/').json()['variation_7j'], 50.0)


class ClotureTests(Base):
    def cloturer(self, lot, **corps):
        return api(self.admin).post(f'/api/lots-dedouanement/{lot.pk}/cloturer/', corps, format='json')

    def test_cloture_directe_quand_tout_est_livre(self):
        lot = self.lot(); self.colis(volume=Decimal('16.5'), statut='livre', lot=lot)
        self.assertTrue(self.lire(lot)['pret_a_cloturer'])
        r = self.cloturer(lot)
        self.assertEqual(r.status_code, 200, r.data)
        self.assertEqual(r.json()['statut'], 'cloture')
        lot.refresh_from_db()
        self.assertIsNotNone(lot.cloture_le); self.assertEqual(lot.cloture_par_id, self.admin.pk)
        entree = JournalActivite.objects.get(action='cloture_lot')
        self.assertEqual((entree.details['pourcentage'], entree.details['non_livres']), (50.0, 0))

    def test_colis_non_livres_demandent_une_confirmation_explicite(self):
        lot = self.lot(); self.colis(statut='livre', lot=lot); self.colis(statut='en_transit', lot=lot); self.colis(statut='embarque', lot=lot)
        r = self.cloturer(lot)
        self.assertEqual(r.status_code, 409)
        self.assertEqual((r.json()['code'], r.json()['non_livres']), ('colis_non_livres', 2))
        self.assertIn('2 colis', r.json()['detail'])
        lot.refresh_from_db(); self.assertEqual(lot.statut, 'ouvert')
        self.assertFalse(self.lire(lot)['pret_a_cloturer'])
        self.assertEqual(self.cloturer(lot, forcer=True).status_code, 200)
        lot.refresh_from_db(); self.assertEqual(lot.statut, 'cloture')
        self.assertEqual(JournalActivite.objects.get(action='cloture_lot').details['non_livres'], 2)

    def test_double_cloture_refusee(self):
        lot = self.lot(); self.cloturer(lot)
        r = self.cloturer(lot)
        self.assertEqual(r.status_code, 409)

    def test_un_conteneur_clos_n_accepte_plus_de_colis(self):
        lot = self.lot(); self.cloturer(lot); c = self.colis()
        r = api(self.agent).post('/api/colis/bulk-assign-lot/', {'colis_ids': [c.pk], 'lot_id': lot.pk}, format='json')
        self.assertEqual(r.status_code, 409)
        self.assertIn('clôturé', r.json()['detail'])
        r = api(self.agent).patch(f'/api/colis/{c.pk}/', {'lot': lot.pk}, format='json')
        self.assertEqual(r.status_code, 400)
        self.assertIn('clôturé', str(r.json()['lot']))
        c.refresh_from_db(); self.assertIsNone(c.lot_id)

    def test_un_colis_ne_sort_pas_d_un_conteneur_clos(self):
        lot = self.lot(); c = self.colis(statut='livre', lot=lot); autre = self.lot('AUTRE')
        self.cloturer(lot)
        r = api(self.agent).patch(f'/api/colis/{c.pk}/', {'lot': None}, format='json')
        self.assertEqual(r.status_code, 400)
        r = api(self.agent).post('/api/colis/bulk-assign-lot/', {'colis_ids': [c.pk], 'lot_id': autre.pk}, format='json')
        self.assertEqual(r.json()['count'], 0)
        self.assertIn('clôturé', r.json()['ignores'][0]['raison'])
        c.refresh_from_db(); self.assertEqual(c.lot_id, lot.pk)

    def test_les_colis_livres_ne_sont_pas_charges(self):
        lot = self.lot(); livre = self.colis(statut='livre'); ok = self.colis()
        r = api(self.agent).post('/api/colis/bulk-assign-lot/', {'colis_ids': [livre.pk, ok.pk], 'lot_id': lot.pk}, format='json').json()
        self.assertEqual(r['count'], 1)
        self.assertEqual(r['ignores'][0]['raison'], 'Colis déjà livré.')

    def test_clos_donc_retire_de_l_espace_des_agents_mais_visible_de_l_administrateur(self):
        lot = self.lot('OUVERT'); clos = self.lot('CLOS'); self.cloturer(clos)
        agent = api(self.agent).get('/api/lots-dedouanement/').json()
        codes = [x['numero_besc'] for x in (agent['results'] if 'results' in agent else agent)]
        self.assertEqual(codes, ['OUVERT'])
        self.assertEqual(api(self.agent).get(f'/api/lots-dedouanement/{clos.pk}/').status_code, 404)
        admin_clos = api(self.admin).get('/api/lots-dedouanement/?statut=cloture').json()
        self.assertEqual([x['numero_besc'] for x in (admin_clos['results'] if 'results' in admin_clos else admin_clos)], ['CLOS'])
        admin_tous = api(self.admin).get('/api/lots-dedouanement/').json()
        self.assertEqual(len(admin_tous['results'] if 'results' in admin_tous else admin_tous), 2)

    def test_un_conteneur_clos_ne_se_modifie_ni_ne_se_supprime(self):
        lot = self.lot(); self.cloturer(lot)
        self.assertEqual(api(self.admin).patch(f'/api/lots-dedouanement/{lot.pk}/', {'capacite_m3': '10'}, format='json').status_code, 409)
        r = api(self.admin).delete(f'/api/lots-dedouanement/{lot.pk}/')
        self.assertEqual(r.status_code, 409)
        self.assertIn('archive', r.json()['detail'])
        self.assertTrue(LotDedouanement.objects.filter(pk=lot.pk).exists())

    def test_reouverture_par_l_administrateur_seulement(self):
        lot = self.lot(); self.cloturer(lot)
        self.assertEqual(api(self.agent).post(f'/api/lots-dedouanement/{lot.pk}/rouvrir/').status_code, 403)    # réservé à l'administrateur
        r = api(self.admin).post(f'/api/lots-dedouanement/{lot.pk}/rouvrir/')
        self.assertEqual((r.status_code, r.json()['statut']), (200, 'ouvert'))
        lot.refresh_from_db(); self.assertIsNone(lot.cloture_le)
        self.assertEqual(JournalActivite.objects.filter(action='reouverture_lot').count(), 1)
        self.assertEqual(api(self.admin).post(f'/api/lots-dedouanement/{lot.pk}/rouvrir/').status_code, 409)     # déjà ouvert
        c = self.colis()
        self.assertEqual(api(self.agent).post('/api/colis/bulk-assign-lot/', {'colis_ids': [c.pk], 'lot_id': lot.pk}, format='json').json()['count'], 1)

    def test_suppression_d_un_conteneur_ouvert_detache_ses_colis(self):
        lot = self.lot(); c = self.colis(lot=lot)
        self.assertEqual(api(self.admin).delete(f'/api/lots-dedouanement/{lot.pk}/').status_code, 204)
        c.refresh_from_db(); self.assertIsNone(c.lot_id)


class FrontiereAdminAgentTests(Base):
    def test_un_agent_ne_supprime_jamais_un_colis(self):
        c = self.colis()
        r = api(self.agent).delete(f'/api/colis/{c.pk}/')
        self.assertEqual(r.status_code, 403)
        self.assertTrue(Colis.objects.filter(pk=c.pk).exists())
        self.assertEqual(api(self.admin).delete(f'/api/colis/{c.pk}/').status_code, 204)

    def test_le_client_garde_la_suppression_de_son_colis_declare(self):
        c = self.colis(statut='declaree')
        self.assertEqual(api(self.client_a).delete(f'/api/colis/{c.pk}/').status_code, 204)

    def test_le_chiffre_d_affaires_est_reserve_a_l_administrateur(self):
        stats_agent = api(self.agent).get('/api/admin/stats/').json()
        stats_admin = api(self.admin).get('/api/admin/stats/').json()
        self.assertNotIn('ca_du_mois', stats_agent)
        self.assertIn('ca_du_mois', stats_admin)
        for cle in ('colis_en_cours', 'conteneurs', 'colis_par_etape'):
            self.assertIn(cle, stats_agent)

    def test_un_agent_ne_voit_aucun_paiement(self):
        c = self.colis(); Paiement.objects.create(client=self.client_a, colis=c, montant='5000', methode='mtn_momo', moment='en_ligne', statut='en_attente')
        self.assertEqual(api(self.agent).get('/api/paiements/').status_code, 403)
        self.assertEqual(api(self.admin).get('/api/paiements/').status_code, 200)
        self.assertEqual(api(self.client_a).get('/api/paiements/').status_code, 200)
        p = Paiement.objects.get()
        self.assertEqual(api(self.agent).get(f'/api/paiements/{p.pk}/').status_code, 403)
        self.assertEqual(api(self.agent).post(f'/api/paiements/{p.pk}/confirmer/').status_code, 403)

    def test_les_conteneurs_du_tableau_de_bord(self):
        lot = self.lot('OUV', type_conteneur='20'); self.colis(volume=Decimal('16.5'), lot=lot); self.colis(volume=Decimal('3.3'), statut='livre', lot=lot)
        clos = self.lot('CLOS'); api(self.admin).post(f'/api/lots-dedouanement/{clos.pk}/cloturer/', {}, format='json')
        for user in (self.admin, self.agent):
            conteneurs = api(user).get('/api/admin/stats/').json()['conteneurs']
            self.assertEqual([c['numero_besc'] for c in conteneurs], ['OUV'])                   # les clôturés sont retirés
            c = conteneurs[0]
            self.assertEqual((c['pourcentage'], c['volume_m3'], c['nb_colis'], c['colis_livres'], c['capacite_m3']), (60.0, 19.8, 2, 1, 33.0))
            self.assertEqual(c['variation_7j'], 60.0)
            self.assertGreaterEqual(len(c['evolution']), 2)
            self.assertEqual(c['evolution'][-1]['pourcentage'], 60.0)

    def test_des_colis_sans_volume_sont_comptes_a_part(self):
        lot = self.lot('SANSVOL')
        self.colis(volume=None, lot=lot); self.colis(volume=None, lot=lot); self.colis(volume=Decimal('6.6'), lot=lot)
        for url in (f'/api/lots-dedouanement/{lot.pk}/', '/api/admin/stats/'):
            r = api(self.admin).get(url).json()
            c = r if 'numero_besc' in r else r['conteneurs'][0]
            self.assertEqual((c['colis_count'] if 'colis_count' in c else c['nb_colis'], c['colis_sans_volume'], c['pourcentage']), (3, 2, 20.0))

    def test_aucun_colis_sans_volume_quand_tout_est_renseigne(self):
        lot = self.lot('OKVOL'); self.colis(volume=Decimal('3'), lot=lot)
        self.assertEqual(api(self.admin).get(f'/api/lots-dedouanement/{lot.pk}/').json()['colis_sans_volume'], 0)

    def test_aucun_conteneur_ouvert(self):
        self.assertEqual(api(self.admin).get('/api/admin/stats/').json()['conteneurs'], [])

    def test_le_tableau_de_bord_indique_pret_a_cloturer_et_surcharge(self):
        pret = self.lot('PRET'); self.colis(volume=Decimal('10'), statut='livre', lot=pret); self.colis(volume=Decimal('5'), statut='livre', lot=pret)
        pas_pret = self.lot('ENCOURS'); self.colis(volume=Decimal('10'), statut='livre', lot=pas_pret); self.colis(volume=Decimal('5'), statut='embarque', lot=pas_pret)
        vide = self.lot('VIDE')
        trop = self.lot('TROP'); self.colis(volume=Decimal('40'), statut='livre', lot=trop)
        par_nom = {c['numero_besc']: c for c in api(self.admin).get('/api/admin/stats/').json()['conteneurs']}
        self.assertEqual({n: (c['pret_a_cloturer'], c['surcharge']) for n, c in par_nom.items()},
                         {'PRET': (True, False), 'ENCOURS': (False, False), 'VIDE': (False, False), 'TROP': (True, True)})
        self.assertTrue(all(c['statut'] == 'ouvert' and c['cloture_le'] is None for c in par_nom.values()))


class PhotosDesAgentsTests(Base):
    def setUp(self):
        super().setUp()
        self.dossier = tempfile.TemporaryDirectory(); self.addCleanup(self.dossier.cleanup)
        r = override_settings(MEDIA_ROOT=self.dossier.name); r.enable(); self.addCleanup(r.disable)
        self.c = self.colis()

    def photo(self, agent=None):
        t = io.BytesIO(); Image.new('RGB', (3, 3), 'red').save(t, format='PNG')
        return PhotoColis.objects.create(colis=self.c, agent=agent, type_media='photo', url=SimpleUploadedFile('p.png', t.getvalue(), content_type='image/png'), prise_le=timezone.now())

    def test_un_agent_supprime_ses_photos_pas_celles_d_un_collegue_ni_du_client(self):
        mienne, collegue, du_client = self.photo(self.agent), self.photo(self.agent2), self.photo(None)
        url = lambda p: f'/api/colis/{self.c.code_suivi}/photos/{p.pk}/'
        self.assertEqual(api(self.agent).delete(url(mienne)).status_code, 204)
        for autre in (collegue, du_client):
            r = api(self.agent).delete(url(autre))
            self.assertEqual(r.status_code, 403)
            self.assertEqual(r.json()['detail'], "Vous ne pouvez supprimer que les photos que vous avez prises vous-même.")
            self.assertTrue(PhotoColis.objects.filter(pk=autre.pk).exists())

    def test_l_administrateur_peut_tout_retirer(self):
        for p in (self.photo(self.agent), self.photo(None)):
            self.assertEqual(api(self.admin).delete(f'/api/colis/{self.c.code_suivi}/photos/{p.pk}/').status_code, 204)
