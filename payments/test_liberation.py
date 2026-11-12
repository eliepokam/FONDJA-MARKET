"""Déblocage des paiements coincés (changement de prestataire, tunnel coupé...)."""
from decimal import Decimal
from io import StringIO
from unittest.mock import patch

from django.core.management import call_command
from rest_framework.test import APITestCase, APIClient

from notifications.models import Notification
from shipments.models import Colis
from users.models import Client, Utilisateur
from .models import Paiement
from .services import FreeMoPayError

CMD = 'payments.management.commands.liberer_paiements_bloques.FreeMoPayClient'


class BaseLiberation(APITestCase):
    def setUp(self):
        self.client_user = Client.objects.create(nom='C', telephone='+237677000900', code_client='CL-LB01')
        self.colis = Colis.objects.create(client=self.client_user, code_suivi='FM-LB000001',
                                          mode_transport='maritime', cout_final=Decimal('5000'))

    def paiement(self, reference=None, statut='en_attente', colis=None):
        return Paiement.objects.create(client=self.client_user, colis=colis or self.colis, montant='5000',
                                       methode='mtn_momo', moment='en_ligne', statut=statut,
                                       reference_prestataire=reference, telephone_paiement='+237677123456')

    def lancer(self, *args, statut_prestataire=None, erreur=None):
        out = StringIO()
        with patch(CMD) as cls:
            if erreur:
                cls.return_value.get_status.side_effect = erreur
            elif statut_prestataire:
                cls.return_value.get_status.return_value = {'status': statut_prestataire}
            call_command('liberer_paiements_bloques', *args, stdout=out)
        return out.getvalue()

    def peut_repayer(self):
        api = APIClient(); api.force_authenticate(user=self.client_user)
        return api.post('/api/paiements/', {'colis': self.colis.id, 'methode': 'mtn_momo', 'moment': 'en_ligne',
                                            'telephone_paiement': '677123456'}, format='json').status_code == 201


class CommandeLiberationTests(BaseLiberation):
    def test_simulation_par_defaut_ne_modifie_rien(self):
        p = self.paiement()
        sortie = self.lancer()
        p.refresh_from_db()
        self.assertEqual(p.statut, 'en_attente')
        self.assertIn('SIMULATION', sortie)
        self.assertIn('LIBÉRÉ', sortie)
        self.assertFalse(self.peut_repayer())

    def test_sans_reference_le_paiement_est_libere_et_le_client_peut_repayer(self):
        p = self.paiement()
        self.assertFalse(self.peut_repayer())                       # bloqué
        self.lancer('--appliquer')
        p.refresh_from_db()
        self.assertEqual(p.statut, 'echoue')
        self.assertTrue(self.peut_repayer())                        # débloqué

    def test_avec_reference_sans_prestataire_on_ne_touche_a_rien(self):
        p = self.paiement(reference='FMP-1')
        sortie = self.lancer('--appliquer')
        p.refresh_from_db()
        self.assertEqual(p.statut, 'en_attente')
        self.assertIn('--prestataire', sortie)

    def test_le_prestataire_confirme_un_echec(self):
        p = self.paiement(reference='FMP-1')
        self.lancer('--appliquer', '--prestataire', 'freemopay', statut_prestataire='FAILED')
        p.refresh_from_db()
        self.assertEqual(p.statut, 'echoue')

    def test_le_prestataire_confirme_un_paiement_reussi_on_ne_le_marque_pas_echoue(self):
        p = self.paiement(reference='FMP-1')
        sortie = self.lancer('--appliquer', '--prestataire', 'freemopay', statut_prestataire='SUCCESS')
        p.refresh_from_db()
        self.assertEqual(p.statut, 'paye')
        self.assertIsNotNone(p.paye_le)
        self.assertIn('PAYÉ', sortie)
        self.assertTrue(Notification.objects.filter(utilisateur=self.client_user, contenu__contains='confirmé').exists())

    def test_encore_en_attente_chez_le_prestataire_on_laisse_sauf_forcer(self):
        p = self.paiement(reference='FMP-1')
        self.lancer('--appliquer', '--prestataire', 'freemopay', statut_prestataire='PENDING')
        p.refresh_from_db()
        self.assertEqual(p.statut, 'en_attente')
        self.lancer('--appliquer', '--prestataire', 'freemopay', '--forcer', statut_prestataire='PENDING')
        p.refresh_from_db()
        self.assertEqual(p.statut, 'echoue')

    def test_prestataire_injoignable_on_laisse_sauf_forcer(self):
        p = self.paiement(reference='FMP-1')
        sortie = self.lancer('--appliquer', '--prestataire', 'freemopay', erreur=FreeMoPayError('injoignable'))
        p.refresh_from_db()
        self.assertEqual(p.statut, 'en_attente')
        self.assertIn('injoignable', sortie)
        self.lancer('--appliquer', '--prestataire', 'freemopay', '--forcer', erreur=FreeMoPayError('injoignable'))
        p.refresh_from_db()
        self.assertEqual(p.statut, 'echoue')

    def test_un_paiement_deja_paye_n_est_jamais_touche(self):
        p = self.paiement(reference='FMP-9', statut='paye')
        self.lancer('--appliquer', '--forcer')
        p.refresh_from_db()
        self.assertEqual(p.statut, 'paye')

    def test_filtre_par_identifiant(self):
        autre_colis = Colis.objects.create(client=self.client_user, code_suivi='FM-LB000002',
                                           mode_transport='maritime', cout_final=Decimal('1000'))
        a, b = self.paiement(), self.paiement(colis=autre_colis)
        self.lancer('--appliquer', '--paiement', str(a.pk))
        a.refresh_from_db(); b.refresh_from_db()
        self.assertEqual((a.statut, b.statut), ('echoue', 'en_attente'))


class ActionAdminTests(BaseLiberation):
    def test_action_admin_libere_le_colis(self):
        admin_user = Utilisateur.objects.create_superuser(telephone='+237677000901', nom='Root', password='x')
        self.client.force_login(admin_user)
        p_sans_ref, p_avec_ref, p_paye = self.paiement(), self.paiement(reference='FMP-2'), self.paiement(statut='paye')
        r = self.client.post('/django-admin/payments/paiement/', {
            'action': 'liberer_paiements_en_attente',
            '_selected_action': [p_sans_ref.pk, p_avec_ref.pk, p_paye.pk],
        }, follow=True)
        self.assertEqual(r.status_code, 200)
        for p in (p_sans_ref, p_avec_ref, p_paye):
            p.refresh_from_db()
        self.assertEqual((p_sans_ref.statut, p_avec_ref.statut, p_paye.statut), ('echoue', 'echoue', 'paye'))
        textes = ' '.join(str(m) for m in r.context['messages'])
        self.assertIn('2 paiement(s) marqué(s)', textes)
        self.assertIn('référence prestataire', textes)              # avertissement : vérifier chez le prestataire
