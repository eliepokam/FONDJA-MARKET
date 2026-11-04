"""Purge planifiée : chaque règle, ses limites, et ce qu'elle ne doit JAMAIS toucher."""
import io
import os
import tempfile
from datetime import timedelta
from decimal import Decimal
from io import StringIO

from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.management import call_command
from django.test import override_settings
from django.utils import timezone
from PIL import Image
from rest_framework.test import APITestCase
from rest_framework_simplejwt.token_blacklist.models import BlacklistedToken, OutstandingToken
from rest_framework_simplejwt.tokens import RefreshToken

from complaints.models import MessageReclamation, PhotoMessageReclamation, PhotoReclamation, Reclamation
from core.models import JournalActivite
from core.retention import purger_donnees, reglages
from core.tasks import purger_donnees_task
from notifications.models import Notification
from payments.models import Paiement
from shipments.models import Colis
from tracking.models import PhotoColis
from users.account_deletion import anonymiser_client
from users.models import Administrateur, Client, CodeOtp


def il_y_a(**kw):
    return timezone.now() - timedelta(**kw)


def png(nom='x.png'):
    tampon = io.BytesIO()
    Image.new('RGB', (4, 4), 'red').save(tampon, format='PNG')
    return SimpleUploadedFile(nom, tampon.getvalue(), content_type='image/png')


class BasePurge(APITestCase):
    def setUp(self):
        self.admin = Administrateur.objects.create(nom='Admin', telephone='+237677840099')
        self._n = 0

    def nouveau_client(self, verifie=True, age_jours=0, **kw):
        self._n += 1
        c = Client.objects.create(nom=f'Client {self._n}', telephone=f'+23767784{self._n:04d}', code_client=f'CL-PG{self._n:03d}',
                                  telephone_verifie_le=timezone.now() if verifie else None, **kw)
        if age_jours:
            Client.objects.filter(pk=c.pk).update(cree_le=il_y_a(days=age_jours))
        return Client.objects.get(pk=c.pk)


class ReglesSimplesTests(BasePurge):
    def test_codes_otp_anciens_supprimes_recents_conserves(self):
        c = self.nouveau_client()
        vieux = CodeOtp.objects.create(utilisateur=c, code='111111', expire_le=timezone.now())
        recent = CodeOtp.objects.create(utilisateur=c, code='222222', expire_le=timezone.now() + timedelta(minutes=5))
        CodeOtp.objects.filter(pk=vieux.pk).update(cree_le=il_y_a(days=2))
        r = purger_donnees()
        self.assertEqual(r['codes_otp'], 1)
        self.assertEqual(list(CodeOtp.objects.values_list('pk', flat=True)), [recent.pk])

    def test_jetons_de_session_expires_supprimes_avec_leur_revocation(self):
        c = self.nouveau_client()
        expire, valide = RefreshToken.for_user(c), RefreshToken.for_user(c)
        for t in (expire, valide):
            pass
        OutstandingToken.objects.filter(jti=expire['jti']).update(expires_at=il_y_a(days=1))
        BlacklistedToken.objects.create(token=OutstandingToken.objects.get(jti=expire['jti']))
        r = purger_donnees()
        self.assertEqual(r['jetons_expires'], 1)
        self.assertFalse(OutstandingToken.objects.filter(jti=expire['jti']).exists())
        self.assertFalse(BlacklistedToken.objects.exists())
        self.assertTrue(OutstandingToken.objects.filter(jti=valide['jti']).exists())          # session encore valide : intacte

    def test_notifications_anciennes_supprimees_selon_la_duree_reglee(self):
        c = self.nouveau_client()
        anciennes = [Notification.objects.create(utilisateur=c, type='general', canal='push', contenu=f'n{i}') for i in range(2)]
        recente = Notification.objects.create(utilisateur=c, type='general', canal='push', contenu='récente')
        Notification.objects.filter(pk__in=[n.pk for n in anciennes]).update(cree_le=il_y_a(days=400))
        Notification.objects.filter(pk=recente.pk).update(cree_le=il_y_a(days=100))
        self.assertEqual(purger_donnees()['notifications'], 2)                                # défaut : 365 jours
        with override_settings(RETENTION={'NOTIFICATIONS_JOURS': 30}):
            self.assertEqual(reglages()['NOTIFICATIONS_JOURS'], 30)
            self.assertEqual(purger_donnees()['notifications'], 1)                            # la récente (100 j) dépasse alors 30 j
        self.assertFalse(Notification.objects.exists())

    def test_les_donnees_recentes_ne_sont_jamais_touchees(self):
        c = self.nouveau_client()
        CodeOtp.objects.create(utilisateur=c, code='333333', expire_le=timezone.now() + timedelta(minutes=5))
        Notification.objects.create(utilisateur=c, type='general', canal='push', contenu='neuve')
        RefreshToken.for_user(c)
        r = purger_donnees()
        self.assertEqual(sum(r.values()), 0)
        self.assertEqual((CodeOtp.objects.count(), Notification.objects.count(), OutstandingToken.objects.count()), (1, 1, 1))
        self.assertFalse(JournalActivite.objects.exists())                                   # rien supprimé → rien à journaliser


class ComptesNonVerifiesTests(BasePurge):
    def test_seul_le_compte_ancien_non_verifie_et_sans_historique_est_supprime(self):
        cible = self.nouveau_client(verifie=False, age_jours=45)
        recent = self.nouveau_client(verifie=False, age_jours=5)
        verifie_ancien = self.nouveau_client(verifie=True, age_jours=400)
        avec_colis = self.nouveau_client(verifie=False, age_jours=45)
        Colis.objects.create(client=avec_colis, code_suivi='FM-PG000001', mode_transport='maritime')
        r = purger_donnees()
        self.assertEqual(r['comptes_non_verifies'], 1)
        restants = set(Client.objects.values_list('pk', flat=True))
        self.assertNotIn(cible.pk, restants)
        self.assertEqual(restants, {recent.pk, verifie_ancien.pk, avec_colis.pk})

    def test_le_personnel_et_les_comptes_anonymises_ne_sont_pas_concernes(self):
        ancien_admin = Administrateur.objects.create(nom='Vieux admin', telephone='+237677840098')        # jamais « vérifié » : normal pour le personnel
        Administrateur.objects.filter(pk=ancien_admin.pk).update(cree_le=il_y_a(days=900))
        purger_donnees()
        self.assertTrue(Administrateur.objects.filter(pk=ancien_admin.pk).exists())


class PiecesComptablesTests(BasePurge):
    def compte_supprime(self, age_suppression_jours, code='FM-PG1000'):
        """Un client qui a tout payé et livré, puis a supprimé son compte il y a `age_suppression_jours` jours, avec fichiers sur disque."""
        c = self.nouveau_client()
        colis = Colis.objects.create(client=c, code_suivi=code, mode_transport='maritime', statut='livre', cout_final=Decimal('5000'))
        Paiement.objects.create(client=c, colis=colis, montant='5000', methode='mtn_momo', moment='en_ligne', statut='paye',
                                paye_le=timezone.now(), reference_prestataire='REF')
        photo = PhotoColis.objects.create(colis=colis, url=png('colis.png'), type_media='photo', prise_le=timezone.now())
        rec = Reclamation.objects.create(client=c, colis=colis, type='retard', description='Retard')
        msg = MessageReclamation.objects.create(reclamation=rec, expediteur=c, message='Bonjour')
        p_rec = PhotoReclamation.objects.create(reclamation=rec, url=png('rec.png'))
        p_msg = PhotoMessageReclamation.objects.create(message=msg, url=png('msg.png'))
        anonymiser_client(c)
        Client.objects.filter(pk=c.pk).update(anonymise_le=il_y_a(days=age_suppression_jours))
        return c, colis, [photo.url.path, p_rec.url.path, p_msg.url.path]

    def test_desactivee_par_defaut_meme_pour_un_tres_vieux_compte(self):
        with tempfile.TemporaryDirectory() as media, override_settings(MEDIA_ROOT=media):
            c, colis, chemins = self.compte_supprime(365 * 30)
            r = purger_donnees()
            self.assertEqual((r['pieces_comptables_comptes'], r['pieces_comptables_fichiers']), (0, 0))
            self.assertTrue(Colis.objects.filter(pk=colis.pk).exists())
            self.assertTrue(all(os.path.exists(p) for p in chemins))

    def test_active_supprime_tout_y_compris_les_fichiers_du_disque(self):
        with tempfile.TemporaryDirectory() as media, override_settings(MEDIA_ROOT=media, RETENTION={'PIECES_COMPTABLES_ANNEES': 10}):
            c, colis, chemins = self.compte_supprime(365 * 11)
            self.assertTrue(all(os.path.exists(p) for p in chemins))
            r = purger_donnees()
            self.assertEqual((r['pieces_comptables_comptes'], r['pieces_comptables_fichiers']), (1, 3))
            self.assertFalse(Client.objects.filter(pk=c.pk).exists())
            self.assertFalse(Colis.objects.filter(pk=colis.pk).exists())
            self.assertFalse(Paiement.objects.exists() or Reclamation.objects.exists() or PhotoColis.objects.exists())
            self.assertFalse(any(os.path.exists(p) for p in chemins))              # les images n'encombrent plus le disque

    def test_un_compte_supprime_depuis_moins_longtemps_est_conserve(self):
        with tempfile.TemporaryDirectory() as media, override_settings(MEDIA_ROOT=media, RETENTION={'PIECES_COMPTABLES_ANNEES': 10}):
            c, colis, chemins = self.compte_supprime(365 * 9)
            self.assertEqual(purger_donnees()['pieces_comptables_comptes'], 0)
            self.assertTrue(Colis.objects.filter(pk=colis.pk).exists())
            self.assertTrue(all(os.path.exists(p) for p in chemins))

    def test_un_compte_actif_n_est_jamais_touche_meme_avec_de_tres_vieux_colis(self):
        with tempfile.TemporaryDirectory() as media, override_settings(MEDIA_ROOT=media, RETENTION={'PIECES_COMPTABLES_ANNEES': 1}):
            actif = self.nouveau_client()
            colis = Colis.objects.create(client=actif, code_suivi='FM-PG2000', mode_transport='maritime', statut='livre')
            Colis.objects.filter(pk=colis.pk).update(cree_le=il_y_a(days=365 * 20))
            self.assertEqual(purger_donnees()['pieces_comptables_comptes'], 0)
            self.assertTrue(Colis.objects.filter(pk=colis.pk).exists())

    def test_les_donnees_des_autres_comptes_restent_intactes(self):
        with tempfile.TemporaryDirectory() as media, override_settings(MEDIA_ROOT=media, RETENTION={'PIECES_COMPTABLES_ANNEES': 10}):
            vieux, _, _ = self.compte_supprime(365 * 11, code='FM-PG3001')
            recent, colis_recent, chemins_recents = self.compte_supprime(30, code='FM-PG3002')
            purger_donnees()
            self.assertFalse(Client.objects.filter(pk=vieux.pk).exists())
            self.assertTrue(Client.objects.filter(pk=recent.pk).exists())
            self.assertTrue(Colis.objects.filter(pk=colis_recent.pk).exists())
            self.assertTrue(all(os.path.exists(p) for p in chemins_recents))


class SimulationJournalEtPlanificationTests(BasePurge):
    def donnees_perimees(self):
        c = self.nouveau_client()
        n = Notification.objects.create(utilisateur=c, type='general', canal='push', contenu='vieille')
        Notification.objects.filter(pk=n.pk).update(cree_le=il_y_a(days=500))
        self.nouveau_client(verifie=False, age_jours=60)

    def test_simulation_annonce_sans_rien_supprimer_ni_journaliser(self):
        self.donnees_perimees()
        r = purger_donnees(dry_run=True)
        self.assertEqual((r['notifications'], r['comptes_non_verifies']), (1, 1))
        self.assertEqual((Notification.objects.count(), Client.objects.count()), (1, 2))
        self.assertFalse(JournalActivite.objects.exists())

    def test_la_purge_reelle_est_journalisee_comme_action_du_systeme(self):
        self.donnees_perimees()
        r = purger_donnees()
        entree = JournalActivite.objects.get(action='purge_donnees')
        self.assertIsNone(entree.auteur)
        self.assertEqual(entree.details['notifications'], 1)
        self.assertEqual(entree.details, r)
        from core.serializers import JournalActiviteSerializer
        self.assertEqual(JournalActiviteSerializer(entree).data['auteur_nom'], 'Système')

    def test_idempotente(self):
        self.donnees_perimees()
        purger_donnees()
        self.assertEqual(sum(purger_donnees().values()), 0)
        self.assertEqual(JournalActivite.objects.filter(action='purge_donnees').count(), 1)

    def test_commande_manuelle(self):
        self.donnees_perimees()
        sortie = StringIO()
        call_command('purger_donnees', '--dry-run', stdout=sortie)
        self.assertIn('SIMULATION', sortie.getvalue())
        self.assertIn('DÉSACTIVÉES', sortie.getvalue())
        self.assertEqual(Notification.objects.count(), 1)
        sortie = StringIO()
        call_command('purger_donnees', stdout=sortie)
        self.assertIn('PURGE RÉELLE', sortie.getvalue())
        self.assertEqual(Notification.objects.count(), 0)

    def test_la_tache_planifiee_fait_la_meme_chose(self):
        self.donnees_perimees()
        resultat = purger_donnees_task.apply().get()
        self.assertEqual(resultat['notifications'], 1)
        self.assertEqual(Notification.objects.count(), 0)

    def test_planification_quotidienne_a_3h(self):
        from config.celery import app
        planning = app.conf.beat_schedule['purge-quotidienne-des-donnees']
        self.assertEqual(planning['task'], 'core.tasks.purger_donnees_task')
        self.assertEqual((list(planning['schedule'].hour), list(planning['schedule'].minute)), ([3], [0]))
