"""API des colis et des incidents vue du CLIENT."""
from decimal import Decimal

from rest_framework.test import APITestCase, APIClient

from payments.models import Paiement
from shipments.models import Colis, Incident, LotDedouanement
from users.models import Administrateur, Agent, Client


def api_as(user):
    api = APIClient()
    api.force_authenticate(user=user)
    return api


class BaseClient(APITestCase):
    def setUp(self):
        self.alice = Client.objects.create(nom='Alice', telephone='+237677400001', code_client='CL-SH01')
        self.bob = Client.objects.create(nom='Bob', telephone='+237677400002', code_client='CL-SH02')
        self.admin = Administrateur.objects.create(nom='Admin', telephone='+237677400003')
        self.agent = Agent.objects.create(nom='Agent', telephone='+237677400004', localisation='chine', cree_par=self.admin)
        self.c_alice = Colis.objects.create(client=self.alice, code_suivi='FM-SH000001', mode_transport='maritime')
        self.c_bob = Colis.objects.create(client=self.bob, code_suivi='FM-SH000002', mode_transport='maritime')


class IncidentsVisiblesTests(BaseClient):
    def test_un_client_ne_voit_que_les_incidents_de_ses_colis_ou_de_ses_lots(self):
        lot_commun = LotDedouanement.objects.create(numero_besc='BESC-1', administrateur=self.admin)
        lot_bob = LotDedouanement.objects.create(numero_besc='BESC-2', administrateur=self.admin)
        self.c_alice.lot = lot_commun; self.c_alice.save()
        self.c_bob.lot = lot_bob; self.c_bob.save()

        i_alice = Incident.objects.create(colis=self.c_alice, type='retard', description='Pour Alice')
        i_lot_alice = Incident.objects.create(lot=lot_commun, type='blocage_douane', description='Lot d\'Alice')
        Incident.objects.create(colis=self.c_bob, type='avarie', description='Secret de Bob')
        Incident.objects.create(lot=lot_bob, type='retard', description='Lot de Bob')

        ids = {i['id'] for i in api_as(self.alice).get('/api/incidents/').data['results']}
        self.assertEqual(ids, {i_alice.id, i_lot_alice.id})

    def test_le_personnel_voit_tous_les_incidents(self):
        Incident.objects.create(colis=self.c_alice, type='retard', description='A')
        Incident.objects.create(colis=self.c_bob, type='retard', description='B')
        self.assertEqual(api_as(self.agent).get('/api/incidents/').data['count'], 2)


class SuppressionEtListesTests(BaseClient):
    def test_suppression_d_un_colis_declare_sans_paiement(self):
        self.assertEqual(api_as(self.alice).delete(f'/api/colis/{self.c_alice.id}/').status_code, 204)

    def test_suppression_refusee_avec_un_message_clair_si_un_paiement_existe(self):
        Paiement.objects.create(client=self.alice, colis=self.c_alice, montant='1000', methode='mtn_momo',
                                moment='en_ligne', statut='echoue')
        r = api_as(self.alice).delete(f'/api/colis/{self.c_alice.id}/')
        self.assertEqual(r.status_code, 409)
        self.assertIn('historique de paiement', r.data['detail'])
        self.assertTrue(Colis.objects.filter(pk=self.c_alice.pk).exists())

    def test_suppression_refusee_une_fois_le_colis_pris_en_charge(self):
        self.c_alice.statut = 'embarque'; self.c_alice.save()
        self.assertEqual(api_as(self.alice).delete(f'/api/colis/{self.c_alice.id}/').status_code, 403)

    def test_on_ne_supprime_pas_le_colis_d_un_autre(self):
        self.assertEqual(api_as(self.alice).delete(f'/api/colis/{self.c_bob.id}/').status_code, 404)

    def test_filtre_en_cours_exclut_les_colis_livres(self):
        Colis.objects.create(client=self.alice, code_suivi='FM-SH000003', mode_transport='maritime', statut='livre')
        tous = api_as(self.alice).get('/api/colis/').data['count']
        en_cours = api_as(self.alice).get('/api/colis/?en_cours=true').data
        livres = api_as(self.alice).get('/api/colis/?statut=livre').data['count']
        self.assertEqual((tous, en_cours['count'], livres), (2, 1, 1))
        self.assertNotIn('livre', {c['statut'] for c in en_cours['results']})

    def test_recherche_par_code_dans_ses_propres_colis(self):
        r = api_as(self.alice).get('/api/colis/?search=FM-SH000001')
        self.assertEqual([c['code_suivi'] for c in r.data['results']], ['FM-SH000001'])
        self.assertEqual(api_as(self.alice).get('/api/colis/?search=FM-SH000002').data['count'], 0)   # colis de Bob
