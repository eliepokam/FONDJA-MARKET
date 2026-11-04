"""Espace ADMINISTRATEUR : statistiques, export, journal."""
import csv
import io
from datetime import datetime, timedelta, timezone as dt_timezone
from decimal import Decimal

from django.utils import timezone
from rest_framework.test import APITestCase, APIClient

from complaints.models import Reclamation
from core.models import JournalActivite
from payments.models import Paiement
from shipments.models import Colis
from tracking.models import HistoriqueStatutColis
from users.models import Administrateur, Agent, Client


def api_as(user):
    api = APIClient()
    api.force_authenticate(user=user)
    return api


class BaseStats(APITestCase):
    def setUp(self):
        self.admin = Administrateur.objects.create(nom='Admin', telephone='+237677800001')
        self.agent = Agent.objects.create(nom='Agent', telephone='+237677800002', localisation='cameroun', cree_par=self.admin)
        self.alice = Client.objects.create(nom='Alice', telephone='+237677800003', code_client='CL-ST01')

    def colis(self, code, **kw):
        defaults = dict(client=self.alice, code_suivi=code, mode_transport='maritime')
        defaults.update(kw)
        return Colis.objects.create(**defaults)

    def stats(self):
        return api_as(self.admin).get('/api/admin/stats/').data


class StatistiquesTests(BaseStats):
    def test_sorties_comptent_les_livraisons_enregistrees_aujourd_hui_pas_les_retouches(self):
        ancien = self.colis('FM-ST000001', statut='livre')
        hier = timezone.now() - timedelta(days=1)
        h = HistoriqueStatutColis.objects.create(colis=ancien, statut='livre', agent=self.agent)
        HistoriqueStatutColis.objects.filter(pk=h.pk).update(cree_le=hier)
        Colis.objects.filter(pk=ancien.pk).update(modifie_le=timezone.now())          # simple retouche aujourd'hui
        self.assertEqual(self.stats()['sorties_aujourdhui'], 0)

        aujourd_hui = self.colis('FM-ST000002', statut='en_livraison')
        r = api_as(self.agent).post(f'/api/colis/{aujourd_hui.code_suivi}/statut/', {'statut': 'livre'}, format='json')
        self.assertEqual(r.status_code, 200, r.data)
        self.assertEqual(self.stats()['sorties_aujourdhui'], 1)

    def test_le_ca_du_mois_suit_le_calendrier_local(self):
        c = self.colis('FM-ST000003')
        maintenant = timezone.localtime()
        debut_local = maintenant.replace(day=1, hour=0, minute=30, second=0, microsecond=0)        # 00h30 locale le 1er
        veille_locale = debut_local - timedelta(hours=1)                                         # 23h30 locale, mois précédent

        def payer(montant, quand):
            p = Paiement.objects.create(client=self.alice, colis=c, montant=montant, methode='mtn_momo', moment='en_ligne', statut='paye')
            Paiement.objects.filter(pk=p.pk).update(paye_le=quand)

        payer(Decimal('10000'), debut_local)
        payer(Decimal('7000'), veille_locale)
        self.assertEqual(self.stats()['ca_du_mois'], 10000.0)      # 7 000 appartient au mois précédent

    def test_cbm_en_attente_utilise_le_volume_mesure_sinon_declare(self):
        self.colis('FM-ST000004', volume_m3=Decimal('2.000'), volume_declare_m3=Decimal('9.000'))      # mesure prioritaire
        self.colis('FM-ST000005', volume_declare_m3=Decimal('1.500'))                                  # repli sur le déclaré
        self.colis('FM-ST000006', statut='embarque', volume_m3=Decimal('40.000'))                       # déjà parti : exclu
        self.colis('FM-ST000007', mode_transport='aerien_standard', poids_kg=Decimal('5'))              # aérien : exclu
        self.assertAlmostEqual(self.stats()['cbm_en_attente'], 3.5)

    def test_tous_les_statuts_sont_comptes(self):
        self.colis('FM-ST000008', statut='livre')
        etapes = self.stats()['colis_par_etape']
        self.assertEqual(set(etapes), {v for v, _ in Colis.Statut.choices})
        self.assertEqual(etapes['livre'], 1)


def lire(reponse):
    texte = reponse.content.decode('utf-8')
    return texte, list(csv.reader(io.StringIO(texte.lstrip('\ufeff')), delimiter=';'))


class ExportTests(BaseStats):
    def exporter(self, user=None, **params):
        return api_as(user or self.admin).get('/api/admin/export/', params)

    def test_le_bom_n_est_ecrit_qu_une_fois_et_le_separateur_est_le_point_virgule(self):
        for i in range(5):
            self.colis(f'FM-EX00000{i}')
        r = self.exporter(types='colis')
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.content.count(b'\xef\xbb\xbf'), 1)          # avant : un BOM par ligne
        self.assertTrue(r.content.startswith(b'\xef\xbb\xbf'))
        self.assertTrue(r['Content-Type'].startswith('text/csv'))
        texte, lignes = lire(r)
        self.assertEqual(lignes[1], ['Code suivi', 'Client', 'Statut', 'Mode transport', 'Coût final', 'Créé le'])
        self.assertEqual(len(lignes[2]), 6)                              # 6 colonnes, pas une seule cellule

    def test_les_dates_sont_celles_du_cameroun(self):
        c = self.colis('FM-EX000010')
        tard_utc = datetime(2026, 3, 10, 23, 30, tzinfo=dt_timezone.utc)                    # = 00h30 le 11 à Douala
        Colis.objects.filter(pk=c.pk).update(cree_le=tard_utc)
        _, lignes = lire(self.exporter(types='colis'))
        self.assertEqual(lignes[2][5], '11/03/2026')

    def test_filtre_par_periode_et_type(self):
        a = self.colis('FM-EX000011')
        b = self.colis('FM-EX000012')
        Colis.objects.filter(pk=a.pk).update(cree_le=timezone.make_aware(datetime(2026, 1, 15, 12, 0)))
        Colis.objects.filter(pk=b.pk).update(cree_le=timezone.make_aware(datetime(2026, 2, 15, 12, 0)))
        texte, _ = lire(self.exporter(types='colis', start='2026-02-01', end='2026-02-28'))
        self.assertIn('FM-EX000012', texte)
        self.assertNotIn('FM-EX000011', texte)
        self.assertNotIn('PAIEMENTS', texte)

    def test_seuls_les_paiements_payes_sont_exportes(self):
        c = self.colis('FM-EX000013')
        Paiement.objects.create(client=self.alice, colis=c, montant='1000', methode='mtn_momo', moment='en_ligne', statut='paye', paye_le=timezone.now())
        Paiement.objects.create(client=self.alice, colis=c, montant='2000', methode='mtn_momo', moment='en_ligne', statut='echoue')
        _, lignes = lire(self.exporter(types='paiements'))
        montants = [l[2] for l in lignes if len(l) == 5 and l[2].replace('.', '').isdigit()]
        self.assertEqual(montants, ['1000.00'])

    def test_les_noms_ne_peuvent_pas_injecter_de_formule(self):
        evil = Client.objects.create(nom='=HYPERLINK("http://x")', telephone='+237677800009', code_client='CL-EVIL')
        self.colis('FM-EX000014', client=evil)
        texte, _ = lire(self.exporter(types='colis'))
        self.assertIn("'=HYPERLINK", texte)

    def test_parametres_invalides_donnent_400_et_non_500(self):
        for params in ({'types': 'colis', 'start': '31/01/2026'}, {'types': 'colis', 'start': '2026-02-01', 'end': '2026-01-01'},
                       {}, {'types': ''}, {'types': 'colis,secrets'}):
            self.assertEqual(self.exporter(**params).status_code, 400, params)

    def test_reserve_aux_administrateurs(self):
        self.assertEqual(self.exporter(user=self.agent, types='colis').status_code, 403)
        self.assertEqual(self.exporter(user=self.alice, types='colis').status_code, 403)

    def test_un_export_est_journalise(self):
        self.exporter(types='colis,paiements', start='2026-02-01', end='2026-02-28')
        entree = JournalActivite.objects.get(action='export_donnees')
        self.assertEqual(entree.cible_description, 'colis, paiements')
        self.assertEqual((entree.details['debut'], entree.details['fin']), ('2026-02-01', '2026-02-28'))
        self.assertEqual(entree.auteur_id, self.admin.pk)
        self.exporter(types='colis', start='mauvaise')                       # un export refusé n'est pas journalisé
        self.assertEqual(JournalActivite.objects.filter(action='export_donnees').count(), 1)


class JournalTests(BaseStats):
    def test_tout_l_historique_est_consultable_par_pages(self):
        for i in range(45):
            JournalActivite.objects.create(auteur=self.admin, action='creation_tarif', cible_description=f'Tarif {i}')
        r1 = api_as(self.admin).get('/api/admin/journal/').data
        r3 = api_as(self.admin).get('/api/admin/journal/?page=3').data
        self.assertEqual(r1['count'], 45)                                    # avant : plafonné à 100, jamais plus de 20 visibles
        self.assertEqual(len(r1['results']), 20)
        self.assertEqual(len(r3['results']), 5)
        self.assertEqual(r1['results'][0]['cible_description'], 'Tarif 44')  # le plus récent d'abord

    def test_reserve_aux_administrateurs(self):
        self.assertEqual(api_as(self.agent).get('/api/admin/journal/').status_code, 403)


class RoutesDeDebugTests(APITestCase):
    def test_la_route_de_test_publique_n_existe_plus(self):
        self.assertEqual(self.client.get('/test-vivant/').status_code, 404)
