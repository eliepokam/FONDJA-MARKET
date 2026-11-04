"""Grille tarifaire publique (landing page)."""
from decimal import Decimal

from rest_framework.test import APITestCase, APIClient

from shipments.models import Tarif
from users.models import Administrateur, Client


class TarifsPublicsTests(APITestCase):
    def setUp(self):
        self.admin = Administrateur.objects.create(nom='Admin', telephone='+237677950001')
        Tarif.objects.create(mode_transport='maritime', prix_par_m3=Decimal('250000'), delai_min_jours=30, delai_max_jours=45,
                             actif=True, administrateur=self.admin)
        Tarif.objects.create(mode_transport='aerien_express', prix_par_kg=Decimal('15000'), delai_min_jours=5, delai_max_jours=10,
                             actif=True, administrateur=self.admin)
        Tarif.objects.create(mode_transport='aerien_standard', prix_par_kg=Decimal('9000'), actif=False)

    def lire(self, api=None):
        return (api or APIClient()).get('/api/public/tarifs/')

    def test_un_visiteur_non_connecte_lit_les_tarifs_actifs(self):
        r = self.lire()
        self.assertEqual(r.status_code, 200)
        modes = [t['mode_transport'] for t in r.json()]
        self.assertEqual(sorted(modes), ['aerien_express', 'maritime'])          # l'inactif n'est pas exposé

    def test_reponse_sans_pagination_et_sans_donnee_interne(self):
        data = self.lire().json()
        self.assertIsInstance(data, list)                                        # pas d'enveloppe {count, results}
        for tarif in data:
            self.assertEqual(set(tarif), {'mode_transport', 'prix_par_kg', 'prix_par_m3', 'delai_min_jours', 'delai_max_jours', 'devise'})

    def test_les_valeurs_sont_celles_du_tarif(self):
        maritime = next(t for t in self.lire().json() if t['mode_transport'] == 'maritime')
        self.assertEqual((maritime['prix_par_m3'], maritime['prix_par_kg']), ('250000.00', None))
        self.assertEqual((maritime['delai_min_jours'], maritime['delai_max_jours'], maritime['devise']), (30, 45, 'XAF'))

    def test_mise_en_cache_cinq_minutes(self):
        cc = self.lire()['Cache-Control']
        self.assertIn('public', cc)
        self.assertIn('max-age=300', cc)

    def test_liste_vide_si_aucun_tarif_actif(self):
        Tarif.objects.update(actif=False)
        r = self.lire()
        self.assertEqual((r.status_code, r.json()), (200, []))

    def test_pas_de_limitation_de_debit_sur_cette_lecture_publique(self):
        api = APIClient()
        self.assertTrue(all(self.lire(api).status_code == 200 for _ in range(30)))   # l'anonyme est limité à 20/h ailleurs

    def test_un_client_connecte_peut_aussi_la_lire_et_l_ecriture_reste_interdite(self):
        client = Client.objects.create(nom='Alice', telephone='+237677950002', code_client='CL-PT01')
        api = APIClient(); api.force_authenticate(user=client)
        self.assertEqual(self.lire(api).status_code, 200)
        self.assertEqual(api.post('/api/public/tarifs/', {'mode_transport': 'maritime'}, format='json').status_code, 405)

    def test_la_route_privee_reste_protegee(self):
        self.assertEqual(APIClient().get('/api/tarifs/').status_code, 401)
