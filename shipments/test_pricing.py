"""Coût automatique à la confirmation des mesures par l'agent (PATCH /api/colis/<id>/)."""
from decimal import Decimal

from rest_framework.test import APITestCase, APIClient

from notifications.models import Notification
from payments.models import Paiement
from users.models import Client, Administrateur, Agent
from .models import Colis, Tarif
from .pricing import CoutIndisponible, calculer_cout


def creer_client(tel='+237677000400', code='CL-PRC1'):
    c = Client.objects.create(nom='Client', telephone=tel, code_client=code)
    c.set_password('x'); c.save()
    return c


def api_as(user):
    api = APIClient()
    api.force_authenticate(user=user)
    return api


class CalculCoutTests(APITestCase):
    def setUp(self):
        self.client_user = creer_client()

    def colis(self, mode='aerien_standard', **kw):
        return Colis.objects.create(client=self.client_user, code_suivi=f"FM-C{Colis.objects.count():07d}",
                                    mode_transport=mode, **kw)

    def test_aerien_au_kg(self):
        Tarif.objects.create(mode_transport='aerien_standard', prix_par_kg=Decimal('3500'))
        self.assertEqual(calculer_cout(self.colis(poids_kg=Decimal('5.5'))), Decimal('19250'))

    def test_maritime_au_m3(self):
        Tarif.objects.create(mode_transport='maritime', prix_par_m3=Decimal('250000'), prix_par_kg=Decimal('1000'))
        self.assertEqual(calculer_cout(self.colis('maritime', volume_m3=Decimal('0.04'), poids_kg=Decimal('50'))),
                         Decimal('10000'))

    def test_maritime_sans_volume_repli_sur_le_poids(self):
        Tarif.objects.create(mode_transport='maritime', prix_par_m3=Decimal('250000'), prix_par_kg=Decimal('1000'))
        self.assertEqual(calculer_cout(self.colis('maritime', poids_kg=Decimal('50'))), Decimal('50000'))

    def test_aucun_tarif_actif(self):
        with self.assertRaises(CoutIndisponible):
            calculer_cout(self.colis(poids_kg=Decimal('2')))

    def test_tarif_inactif_ignore_et_le_plus_recent_gagne(self):
        Tarif.objects.create(mode_transport='aerien_standard', prix_par_kg=Decimal('9999'), actif=False)
        Tarif.objects.create(mode_transport='aerien_standard', prix_par_kg=Decimal('1000'))
        Tarif.objects.create(mode_transport='aerien_standard', prix_par_kg=Decimal('2000'))
        self.assertEqual(calculer_cout(self.colis(poids_kg=Decimal('1'))), Decimal('2000'))

    def test_arrondi_a_l_xaf_entier(self):
        Tarif.objects.create(mode_transport='aerien_standard', prix_par_kg=Decimal('3333.33'))
        self.assertEqual(calculer_cout(self.colis(poids_kg=Decimal('1.5'))), Decimal('5000'))

    def test_devise_non_xaf_refusee(self):
        Tarif.objects.create(mode_transport='aerien_standard', prix_par_kg=Decimal('10'), devise='USD')
        with self.assertRaises(CoutIndisponible):
            calculer_cout(self.colis(poids_kg=Decimal('2')))

    def test_cout_nul_refuse(self):
        Tarif.objects.create(mode_transport='aerien_standard', prix_par_kg=Decimal('0.01'))
        with self.assertRaises(CoutIndisponible):
            calculer_cout(self.colis(poids_kg=Decimal('0.01')))


class ConfirmationDesMesuresTests(APITestCase):
    def setUp(self):
        self.client_user = creer_client()
        admin = Administrateur.objects.create(nom='Admin', telephone='+237677000401'); admin.set_password('x'); admin.save()
        self.agent = Agent.objects.create(nom='Agent', telephone='+237677000402', localisation='chine', cree_par=admin)
        self.agent.set_password('x'); self.agent.save()
        Tarif.objects.create(mode_transport='aerien_standard', prix_par_kg=Decimal('3500'))
        self.colis = Colis.objects.create(client=self.client_user, code_suivi='FM-MES00001',
                                          mode_transport='aerien_standard')
        self.url = f'/api/colis/{self.colis.id}/'

    def patch(self, data, user=None):
        return api_as(user or self.agent).patch(self.url, data, format='json')

    def test_les_mesures_declenchent_le_cout_et_la_notification(self):
        r = self.patch({'poids_kg': '5.5'})
        self.assertEqual(r.status_code, 200, r.data)
        self.colis.refresh_from_db()
        self.assertEqual(self.colis.cout_final, Decimal('19250'))
        notif = Notification.objects.get(utilisateur=self.client_user, colis=self.colis)
        self.assertEqual(notif.type, 'paiement')
        self.assertIn('19 250 XAF', notif.contenu)

    def test_rejouer_les_memes_mesures_ne_renotifie_pas(self):
        self.patch({'poids_kg': '5.5'})
        self.patch({'poids_kg': '5.5'})
        self.assertEqual(Notification.objects.filter(colis=self.colis).count(), 1)

    def test_corriger_les_mesures_recalcule_et_renotifie(self):
        self.patch({'poids_kg': '5.5'})
        self.patch({'poids_kg': '6'})
        self.colis.refresh_from_db()
        self.assertEqual(self.colis.cout_final, Decimal('21000'))
        self.assertEqual(Notification.objects.filter(colis=self.colis).count(), 2)

    def test_sans_tarif_les_mesures_sont_gardees_et_le_cout_reste_vide(self):
        Tarif.objects.all().delete()
        r = self.patch({'poids_kg': '5.5'})
        self.assertEqual(r.status_code, 200)
        self.colis.refresh_from_db()
        self.assertEqual(self.colis.poids_kg, Decimal('5.50'))
        self.assertIsNone(self.colis.cout_final)
        self.assertFalse(Notification.objects.exists())

    def test_un_ancien_cout_devenu_faux_est_efface(self):
        self.patch({'poids_kg': '5.5'})
        Tarif.objects.all().delete()
        self.patch({'poids_kg': '9'})
        self.colis.refresh_from_db()
        self.assertIsNone(self.colis.cout_final)

    def test_modifier_autre_chose_ne_touche_pas_au_cout(self):
        self.patch({'poids_kg': '5.5'})
        r = self.patch({'numero_besc': 'BESC-1'})
        self.assertEqual(r.status_code, 200)
        self.colis.refresh_from_db()
        self.assertEqual(self.colis.cout_final, Decimal('19250'))
        self.assertEqual(Notification.objects.filter(colis=self.colis).count(), 1)

    def test_mesures_verrouillees_des_qu_un_paiement_est_actif(self):
        self.patch({'poids_kg': '5.5'})
        Paiement.objects.create(client=self.client_user, colis=self.colis, montant='19250',
                                methode='mtn_momo', moment='en_ligne', statut='en_attente')
        r = self.patch({'poids_kg': '1'})
        self.assertEqual(r.status_code, 400)
        self.colis.refresh_from_db()
        self.assertEqual(self.colis.cout_final, Decimal('19250'))

    def test_mesures_modifiables_si_le_paiement_a_echoue(self):
        self.patch({'poids_kg': '5.5'})
        Paiement.objects.create(client=self.client_user, colis=self.colis, montant='19250',
                                methode='mtn_momo', moment='en_ligne', statut='echoue')
        self.assertEqual(self.patch({'poids_kg': '6'}).status_code, 200)

    def test_le_client_ne_peut_pas_fixer_les_mesures_ni_le_cout(self):
        r = self.patch({'poids_kg': '0.01', 'cout_final': '1'}, user=self.client_user)
        self.assertEqual(r.status_code, 403)
        self.colis.refresh_from_db()
        self.assertIsNone(self.colis.cout_final)
