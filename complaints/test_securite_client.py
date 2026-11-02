"""Réclamations côté CLIENT : on ne touche qu'à ses propres colis, ses propres fils, ses propres photos."""
import io
from decimal import Decimal

from django.core.files.uploadedfile import SimpleUploadedFile
from PIL import Image
from rest_framework.test import APITestCase, APIClient

from complaints.models import MessageReclamation, Reclamation
from shipments.models import Colis
from users.models import Administrateur, Agent, Client


def png(nom='photo.png'):
    tampon = io.BytesIO()
    Image.new('RGB', (8, 8), 'blue').save(tampon, format='PNG')
    return SimpleUploadedFile(nom, tampon.getvalue(), content_type='image/png')


def api_as(user):
    api = APIClient()
    api.force_authenticate(user=user)
    return api


class BaseReclamation(APITestCase):
    def setUp(self):
        self.alice = Client.objects.create(nom='Alice', telephone='+237677200001', code_client='CL-RC01')
        self.bob = Client.objects.create(nom='Bob', telephone='+237677200002', code_client='CL-RC02')
        self.admin = Administrateur.objects.create(nom='Admin', telephone='+237677200003')
        self.agent = Agent.objects.create(nom='Agent', telephone='+237677200004', localisation='cameroun', cree_par=self.admin)
        self.colis_alice = Colis.objects.create(client=self.alice, code_suivi='FM-RC000001', mode_transport='maritime')
        self.colis_bob = Colis.objects.create(client=self.bob, code_suivi='FM-RC000002', mode_transport='maritime')

    def reclamation_alice(self, **kw):
        defaults = dict(client=self.alice, colis=self.colis_alice, type='retard', description='Colis en retard de trois jours')
        defaults.update(kw)
        return Reclamation.objects.create(**defaults)


class CreationEtModificationTests(BaseReclamation):
    def test_creation_sur_son_propre_colis(self):
        r = api_as(self.alice).post('/api/reclamations/', {'colis': self.colis_alice.id, 'type': 'retard',
                                                          'description': 'Toujours rien reçu'}, format='json')
        self.assertEqual(r.status_code, 201, r.data)

    def test_impossible_d_ouvrir_une_reclamation_sur_le_colis_d_un_autre(self):
        r = api_as(self.alice).post('/api/reclamations/', {'colis': self.colis_bob.id, 'type': 'retard',
                                                          'description': 'Je veux voir le colis de Bob'}, format='json')
        self.assertEqual(r.status_code, 400)
        self.assertIn('colis', r.data)
        self.assertFalse(Reclamation.objects.filter(colis=self.colis_bob).exists())

    def test_un_client_ne_modifie_ni_ne_supprime_sa_reclamation(self):
        rec = self.reclamation_alice()
        api = api_as(self.alice)
        self.assertEqual(api.patch(f'/api/reclamations/{rec.id}/', {'colis': self.colis_bob.id}, format='json').status_code, 403)
        self.assertEqual(api.patch(f'/api/reclamations/{rec.id}/', {'description': 'modifiée'}, format='json').status_code, 403)
        self.assertEqual(api.delete(f'/api/reclamations/{rec.id}/').status_code, 403)
        rec.refresh_from_db()
        self.assertEqual(rec.colis_id, self.colis_alice.id)

    def test_un_client_ne_voit_pas_la_reclamation_d_un_autre(self):
        rec = self.reclamation_alice()
        self.assertEqual(api_as(self.bob).get(f'/api/reclamations/{rec.id}/').status_code, 404)
        self.assertEqual(api_as(self.bob).get('/api/reclamations/').data['count'], 0)

    def test_le_personnel_change_le_statut_mais_pas_le_contenu(self):
        rec = self.reclamation_alice()
        r = api_as(self.agent).patch(f'/api/reclamations/{rec.id}/', {'statut': 'resolue', 'description': 'réécrite'}, format='json')
        self.assertEqual(r.status_code, 200, r.data)
        rec.refresh_from_db()
        self.assertEqual(rec.statut, 'resolue')
        self.assertEqual(rec.description, 'Colis en retard de trois jours')

    def test_seul_un_administrateur_supprime(self):
        rec = self.reclamation_alice()
        self.assertEqual(api_as(self.agent).delete(f'/api/reclamations/{rec.id}/').status_code, 403)
        self.assertEqual(api_as(self.admin).delete(f'/api/reclamations/{rec.id}/').status_code, 204)


class PhotosReclamationTests(BaseReclamation):
    def test_le_proprietaire_ajoute_et_liste_ses_photos_sur_la_bonne_route(self):
        rec = self.reclamation_alice()
        api = api_as(self.alice)
        r = api.post(f'/api/reclamations/{rec.id}/photos/', {'url': png()}, format='multipart')
        self.assertEqual(r.status_code, 201, r.data)
        self.assertEqual(len(api.get(f'/api/reclamations/{rec.id}/photos/').data['results']), 1)

    def test_un_autre_client_ne_peut_ni_lister_ni_ajouter(self):
        rec = self.reclamation_alice()
        bob = api_as(self.bob)
        self.assertEqual(bob.get(f'/api/reclamations/{rec.id}/photos/').status_code, 403)
        self.assertEqual(bob.post(f'/api/reclamations/{rec.id}/photos/', {'url': png()}, format='multipart').status_code, 403)

    def test_un_fichier_qui_n_est_pas_une_image_est_refuse(self):
        rec = self.reclamation_alice()
        faux = SimpleUploadedFile('virus.png', b'<script>alert(1)</script>', content_type='image/png')
        r = api_as(self.alice).post(f'/api/reclamations/{rec.id}/photos/', {'url': faux}, format='multipart')
        self.assertEqual(r.status_code, 400)


class MessagesReclamationTests(BaseReclamation):
    def poster(self, user, rec, **data):
        return api_as(user).post(f'/api/reclamations/{rec.id}/messages/', {'message': 'Bonjour', **data}, format='multipart')

    def test_message_avec_photo_valide(self):
        rec = self.reclamation_alice()
        r = self.poster(self.alice, rec, photo=png())
        self.assertEqual(r.status_code, 201, r.data)
        self.assertEqual(MessageReclamation.objects.get(pk=r.data['id']).photos.count(), 1)

    def test_photo_invalide_refusee_et_aucun_message_cree(self):
        rec = self.reclamation_alice()
        r = self.poster(self.alice, rec, photo=SimpleUploadedFile('page.html', b'<html></html>', content_type='text/html'))
        self.assertEqual(r.status_code, 400)
        self.assertEqual(rec.messages.count(), 0)

    def test_photo_trop_lourde_refusee(self):
        rec = self.reclamation_alice()
        enorme = SimpleUploadedFile('enorme.png', b'\x89PNG' + b'0' * (9 * 1024 * 1024), content_type='image/png')
        r = self.poster(self.alice, rec, photo=enorme)
        self.assertEqual(r.status_code, 400)
        self.assertEqual(rec.messages.count(), 0)

    def test_un_etranger_ne_peut_pas_ecrire(self):
        rec = self.reclamation_alice()
        self.assertEqual(self.poster(self.bob, rec).status_code, 403)

    def test_le_client_ne_peut_plus_ecrire_une_fois_la_reclamation_cloturee_mais_le_personnel_oui(self):
        for statut in ('resolue', 'rejetee'):
            rec = self.reclamation_alice(statut=statut)
            self.assertEqual(self.poster(self.alice, rec).status_code, 403, statut)
            self.assertEqual(self.poster(self.agent, rec).status_code, 201, statut)

    def test_la_reponse_du_personnel_notifie_le_client(self):
        from notifications.models import Notification
        rec = self.reclamation_alice()
        self.poster(self.agent, rec)
        self.assertTrue(Notification.objects.filter(utilisateur=self.alice, type='reclamation').exists())


class TraitementParLePersonnelTests(BaseReclamation):
    def changer(self, user, rec, **data):
        return api_as(user).patch(f'/api/reclamations/{rec.id}/', data, format='json')

    def test_la_prise_en_charge_assigne_et_previent_le_client(self):
        rec = self.reclamation_alice()
        r = self.changer(self.agent, rec, statut='en_cours')
        self.assertEqual(r.status_code, 200, r.data)
        rec.refresh_from_db()
        self.assertEqual((rec.statut, rec.agent_assigne_id), ('en_cours', self.agent.pk))
        from notifications.models import Notification
        self.assertTrue(Notification.objects.filter(utilisateur=self.alice, contenu__contains='prise en charge').exists())

    def test_une_assignation_existante_n_est_pas_ecrasee(self):
        rec = self.reclamation_alice(agent_assigne=self.admin)
        self.changer(self.agent, rec, statut='en_cours')
        rec.refresh_from_db()
        self.assertEqual(rec.agent_assigne_id, self.admin.pk)

    def test_resolution_et_rejet_previennent_le_client(self):
        from notifications.models import Notification
        for statut, mot in (('resolue', 'résolue'), ('rejetee', 'rejetée')):
            rec = self.reclamation_alice()
            self.changer(self.agent, rec, statut=statut)
            self.assertTrue(Notification.objects.filter(utilisateur=self.alice, contenu__contains=mot).exists(), statut)

    def test_un_statut_inchange_ne_renotifie_pas(self):
        from notifications.models import Notification
        rec = self.reclamation_alice()
        self.changer(self.agent, rec, statut='en_cours')
        avant = Notification.objects.filter(utilisateur=self.alice).count()
        self.changer(self.agent, rec, statut='en_cours')
        self.assertEqual(Notification.objects.filter(utilisateur=self.alice).count(), avant)

    def test_un_statut_invalide_est_refuse(self):
        rec = self.reclamation_alice()
        self.assertEqual(self.changer(self.agent, rec, statut='inconnu').status_code, 400)
