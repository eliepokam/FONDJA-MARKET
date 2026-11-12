"""Retours client : modification d'un colis avant réception, adresses de transit, coordonnées, photos et espace médias."""
import io
import os
import tempfile
from decimal import Decimal

from django.core.cache import cache
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import override_settings
from django.utils import timezone
from PIL import Image
from rest_framework.test import APITestCase, APIClient

from core.models import JournalActivite
from shipments.models import AdresseTransit, Colis
from tracking.models import PhotoColis
from users.models import Administrateur, Agent, Client

AIR = "广东省广州市白云区三元里大道北站五福服装城 3C020  收货人：Michael 电话：135 4444 0536"
MER = "广东省佛山市南海区里水镇西线东路甘蔗工业区内138号之六，广东爱德宝食品有限公司  + Mary777"


def png(nom='p.png'):
    tampon = io.BytesIO()
    Image.new('RGB', (4, 4), 'red').save(tampon, format='PNG')
    return SimpleUploadedFile(nom, tampon.getvalue(), content_type='image/png')


def api_pour(user):
    a = APIClient(); a.force_authenticate(user=user); return a


class Base(APITestCase):
    def setUp(self):
        cache.clear()
        self.client_a = Client.objects.create(nom='Awa', telephone='+237677996001', code_client='CL-RC01', telephone_verifie_le=timezone.now())
        self.client_b = Client.objects.create(nom='Bruno', telephone='+237677996002', code_client='CL-RC02', telephone_verifie_le=timezone.now())
        self.admin = Administrateur.objects.create(nom='Admin', telephone='+237677996003')
        self.agent = Agent.objects.create(nom='Agent', telephone='+237677996004', localisation='chine', cree_par=self.admin)
        self.colis = Colis.objects.create(client=self.client_a, code_suivi='FM-RC000001', mode_transport='maritime', description='Chaussures')
        self.url = f'/api/colis/{self.colis.pk}/'


class ModificationAvantReceptionTests(Base):
    def test_le_client_modifie_son_colis_declare(self):
        r = api_pour(self.client_a).patch(self.url, {'description': 'Sacs x12', 'mode_transport': 'aerien_express',
                                                     'poids_declare_kg': '12.5', 'volume_declare_m3': '0.35'}, format='json')
        self.assertEqual(r.status_code, 200, r.data)
        self.colis.refresh_from_db()
        self.assertEqual((self.colis.description, self.colis.mode_transport, self.colis.poids_declare_kg, self.colis.volume_declare_m3),
                         ('Sacs x12', 'aerien_express', Decimal('12.50'), Decimal('0.350')))

    def test_les_champs_reserves_au_personnel_sont_ignores(self):
        lot_autre = None
        r = api_pour(self.client_a).patch(self.url, {'poids_kg': '99', 'volume_m3': '9', 'cout_final': '1', 'cout_estime': '1', 'statut': 'livre',
                                                     'numero_besc': 'BESC-X', 'date_livraison_estimee': '2030-01-01', 'code_suivi': 'FM-PIRATE',
                                                     'client': self.client_b.pk, 'photo_declaree_url': 'http://x'}, format='json')
        self.assertEqual(r.status_code, 200, r.data)
        self.colis.refresh_from_db()
        self.assertEqual((self.colis.statut, self.colis.code_suivi, self.colis.client_id), ('declaree', 'FM-RC000001', self.client_a.pk))
        self.assertIsNone(self.colis.poids_kg); self.assertIsNone(self.colis.cout_final); self.assertIsNone(self.colis.numero_besc)
        self.assertIsNone(self.colis.date_livraison_estimee); self.assertIsNone(self.colis.photo_declaree_url)

    def test_refus_apres_prise_en_charge_avec_message_francais(self):
        Colis.objects.filter(pk=self.colis.pk).update(statut='recu_entrepot')
        r = api_pour(self.client_a).patch(self.url, {'description': 'Autre'}, format='json')
        self.assertEqual(r.status_code, 403)
        self.assertEqual(r.json()['detail'], "Ce colis a déjà été pris en charge, il ne peut plus être modifié.")
        self.colis.refresh_from_db()
        self.assertEqual(self.colis.description, 'Chaussures')

    def test_le_colis_d_un_autre_client_est_introuvable(self):
        r = api_pour(self.client_b).patch(self.url, {'description': 'Vol'}, format='json')
        self.assertEqual(r.status_code, 404)
        self.colis.refresh_from_db()
        self.assertEqual(self.colis.description, 'Chaussures')

    def test_non_connecte(self):
        self.assertEqual(APIClient().patch(self.url, {'description': 'x'}, format='json').status_code, 401)

    def test_le_personnel_garde_ses_droits(self):
        r = api_pour(self.agent).patch(self.url, {'poids_kg': '5.5'}, format='json')
        self.assertEqual(r.status_code, 200, r.data)

    def test_a_la_creation_un_client_ne_peut_plus_choisir_lot_ni_besc(self):
        r = api_pour(self.client_a).post('/api/colis/', {'description': 'x', 'mode_transport': 'maritime', 'numero_besc': 'BESC-Z',
                                                         'date_livraison_estimee': '2030-01-01'}, format='json')
        self.assertEqual(r.status_code, 201, r.data)
        nouveau = Colis.objects.get(code_suivi=r.json()['code_suivi'])
        self.assertIsNone(nouveau.numero_besc); self.assertIsNone(nouveau.date_livraison_estimee)


class AdressesEtCoordonneesTests(Base):
    def lire(self, user=None):
        return api_pour(user or self.client_a).get('/api/coordonnees/')

    def test_les_deux_adresses_fournies_sont_en_base_au_format_exact(self):
        modes = {a['mode']: a for a in self.lire().json()['adresses']}
        self.assertEqual(set(modes), {'aerien', 'maritime'})
        self.assertEqual(modes['aerien']['texte_a_copier'], AIR)
        self.assertEqual(modes['maritime']['texte_a_copier'], MER)
        self.assertEqual(modes['maritime']['marquage'], 'Mary777')
        self.assertEqual(modes['aerien']['destinataire'], 'Michael')
        self.assertEqual(modes['aerien']['telephone'], '135 4444 0536')

    def test_il_faut_etre_connecte(self):
        self.assertEqual(APIClient().get('/api/coordonnees/').status_code, 401)

    @override_settings(SUPPORT_WHATSAPP='+237 6 56 64 56 49', SUPPORT_EMAIL='support@exemple.cm', SUPPORT_HORAIRES='Lun-Sam 8h-18h')
    def test_contact_du_support_lu_dans_les_reglages_du_serveur(self):
        contact = self.lire().json()['contact']
        self.assertEqual(contact, {'whatsapp': '237656645649', 'email': 'support@exemple.cm', 'horaires': 'Lun-Sam 8h-18h'})

    def test_contact_vide_par_defaut(self):
        self.assertEqual(self.lire().json()['contact'], {'whatsapp': '', 'email': '', 'horaires': ''})

    def test_l_administrateur_modifie_une_adresse_et_c_est_journalise(self):
        r = api_pour(self.admin).patch('/api/admin/adresses-transit/maritime/', {'marquage': 'Mary888', 'instructions': 'Nouveau conseil'}, format='json')
        self.assertEqual(r.status_code, 200, r.data)
        self.assertIn('Mary888', r.json()['texte_a_copier'])
        modes = {a['mode']: a for a in self.lire().json()['adresses']}
        self.assertEqual(modes['maritime']['marquage'], 'Mary888')
        entree = JournalActivite.objects.get(action='modification_adresse_transit')
        self.assertEqual(entree.auteur_id, self.admin.pk)
        self.assertIn('Mary888', str(entree.details))

    def test_mode_non_modifiable_et_adresse_vide_refusee(self):
        api = api_pour(self.admin)
        api.patch('/api/admin/adresses-transit/maritime/', {'mode': 'aerien'}, format='json')
        self.assertEqual(AdresseTransit.objects.filter(mode='maritime').count(), 1)
        r = api.patch('/api/admin/adresses-transit/maritime/', {'adresse': '   '}, format='json')
        self.assertEqual(r.status_code, 400)
        self.assertEqual(r.json()['adresse'], ["Ce champ ne peut être vide."])       # message français de DRF

    def test_une_adresse_desactivee_disparait_cote_client_mais_reste_visible_de_l_administrateur(self):
        api_pour(self.admin).patch('/api/admin/adresses-transit/aerien/', {'actif': False}, format='json')
        self.assertEqual([a['mode'] for a in self.lire().json()['adresses']], ['maritime'])
        self.assertEqual(len(api_pour(self.admin).get('/api/admin/adresses-transit/').json()), 2)

    def test_les_clients_et_agents_ne_peuvent_pas_modifier(self):
        for user in (self.client_a, self.agent):
            self.assertEqual(api_pour(user).patch('/api/admin/adresses-transit/maritime/', {'marquage': 'X'}, format='json').status_code, 403)
        self.assertEqual(AdresseTransit.objects.get(mode='maritime').marquage, 'Mary777')


class PhotosEtEspaceMediasTests(Base):
    def setUp(self):
        super().setUp()
        self.dossier = tempfile.TemporaryDirectory()
        self.addCleanup(self.dossier.cleanup)
        self.reglage = override_settings(MEDIA_ROOT=self.dossier.name)
        self.reglage.enable()
        self.addCleanup(self.reglage.disable)
        self.photos = f'/api/colis/{self.colis.code_suivi}/photos/'

    def photo(self, colis=None, agent=None, type_media='photo'):
        return PhotoColis.objects.create(colis=colis or self.colis, agent=agent, type_media=type_media, url=png(), prise_le=timezone.now())

    # ── suppression
    def test_le_client_supprime_sa_propre_photo_et_le_fichier_disparait(self):
        p = self.photo()
        chemin = p.url.path
        self.assertTrue(os.path.exists(chemin))
        r = api_pour(self.client_a).delete(f'{self.photos}{p.pk}/')
        self.assertEqual(r.status_code, 204)
        self.assertFalse(PhotoColis.objects.filter(pk=p.pk).exists())
        self.assertFalse(os.path.exists(chemin))

    def test_le_client_ne_supprime_pas_une_photo_de_l_equipe(self):
        p = self.photo(agent=self.agent)
        r = api_pour(self.client_a).delete(f'{self.photos}{p.pk}/')
        self.assertEqual(r.status_code, 403)
        self.assertEqual(r.json()['detail'], "Cette photo a été prise par l'équipe : vous ne pouvez pas la supprimer.")
        self.assertTrue(PhotoColis.objects.filter(pk=p.pk).exists())

    def test_plus_de_suppression_apres_prise_en_charge(self):
        p = self.photo()
        Colis.objects.filter(pk=self.colis.pk).update(statut='embarque')
        r = api_pour(self.client_a).delete(f'{self.photos}{p.pk}/')
        self.assertEqual(r.status_code, 403)
        self.assertIn('déjà été pris en charge', r.json()['detail'])

    def test_photo_d_un_autre_client_introuvable_et_le_personnel_peut_supprimer(self):
        p = self.photo(agent=self.agent)
        self.assertEqual(api_pour(self.client_b).delete(f'{self.photos}{p.pk}/').status_code, 404)
        self.assertEqual(api_pour(self.agent).delete(f'{self.photos}{p.pk}/').status_code, 204)

    def test_ajout_par_le_client_marque_la_source(self):
        r = api_pour(self.client_a).post(self.photos, {'photo': png(), 'type_media': 'photo'}, format='multipart')
        self.assertEqual(r.status_code, 201, r.data)
        self.assertEqual(r.json()['source'], 'client')
        liste = api_pour(self.client_a).get(self.photos).json()
        self.assertEqual([x['source'] for x in (liste['results'] if 'results' in liste else liste)], ['client'])

    # ── espace « Photos et vidéos »
    def test_le_client_voit_seulement_les_medias_de_l_equipe_sur_ses_colis(self):
        recue = self.photo(agent=self.agent)
        video = self.photo(agent=self.agent, type_media='video')
        self.photo()                                                                               # ajoutée par le client : pas dans cet espace
        autre = Colis.objects.create(client=self.client_b, code_suivi='FM-RC000002', mode_transport='maritime')
        self.photo(colis=autre, agent=self.agent)                                                  # colis d'un autre client
        r = api_pour(self.client_a).get('/api/medias/')
        self.assertEqual(r.status_code, 200)
        resultats = r.json()['results']
        self.assertEqual([m['id'] for m in resultats], [video.pk, recue.pk])                       # du plus récent au plus ancien
        self.assertEqual({m['source'] for m in resultats}, {'entrepot'})
        self.assertEqual({m['code_suivi'] for m in resultats}, {'FM-RC000001'})
        self.assertEqual(resultats[0]['type_media'], 'video')
        self.assertIn('statut_colis', resultats[0])

    def test_filtre_par_colis(self):
        autre = Colis.objects.create(client=self.client_a, code_suivi='FM-RC000003', mode_transport='maritime')
        self.photo(agent=self.agent); self.photo(colis=autre, agent=self.agent)
        r = api_pour(self.client_a).get('/api/medias/?colis=FM-RC000003').json()
        self.assertEqual({m['code_suivi'] for m in r['results']}, {'FM-RC000003'})

    def test_reserve_aux_clients(self):
        self.assertEqual(APIClient().get('/api/medias/').status_code, 401)
        self.assertEqual(api_pour(self.agent).get('/api/medias/').status_code, 403)
