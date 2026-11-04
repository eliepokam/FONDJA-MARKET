"""Suppression de compte CLIENT : application (mot de passe), web (code SMS/WhatsApp) et administrateur."""
from datetime import timedelta
from decimal import Decimal

from django.core.cache import cache
from django.utils import timezone
from rest_framework.test import APITestCase, APIClient

from complaints.models import MessageReclamation, Reclamation
from core.models import JournalActivite
from notifications.models import FCMToken, Notification
from payments.models import Paiement
from shipments.models import Colis
from users.account_deletion import LIBELLE_SUPPRIME, anonymiser_client, obstacles_suppression, SuppressionImpossible
from users.models import Administrateur, Agent, Client, CodeOtp

MDP = 'Zebre!Orange2026'
TEL = '+237677810001'


def creer_client(tel=TEL, code='CL-DEL01', nom='Awa Ngo', verifie=True):
    c = Client.objects.create(nom=nom, telephone=tel, code_client=code, email='awa@exemple.cm',
                              telephone_verifie_le=timezone.now() if verifie else None)
    c.set_password(MDP)
    c.save()
    return c


def historique_termine(client, code='FM-DEL00001'):
    """Un colis livré ET payé, avec son paiement, une réclamation et des notifications : un client « ordinaire » à anonymiser."""
    colis = Colis.objects.create(client=client, code_suivi=code, mode_transport='maritime', statut='livre', cout_final=Decimal('5000'))
    paiement = Paiement.objects.create(client=client, colis=colis, montant='5000', methode='mtn_momo', moment='en_ligne',
                                       statut='paye', paye_le=timezone.now(), telephone_paiement='+237677999888',
                                       reference_prestataire='REF-1')
    return colis, paiement


class BaseSuppression(APITestCase):
    def setUp(self):
        cache.clear()
        self.admin = Administrateur.objects.create(nom='Admin', telephone='+237677810099')
        self.admin.set_password(MDP); self.admin.save()
        self.client_user = creer_client()
        self.api = APIClient()

    def connecter(self, tel=TEL, mdp=MDP, portail='client'):
        api = APIClient()
        r = api.post('/api/auth/login/', {'telephone': tel, 'password': mdp, 'portail': portail}, format='json')
        self.assertEqual(r.status_code, 200, r.data)
        return api


class ObstaclesTests(BaseSuppression):
    def test_colis_en_cours_paiement_en_attente_et_impaye_bloquent(self):
        self.assertEqual(obstacles_suppression(self.client_user), [])
        Colis.objects.create(client=self.client_user, code_suivi='FM-DEL10001', mode_transport='maritime', statut='embarque')
        livre_impaye = Colis.objects.create(client=self.client_user, code_suivi='FM-DEL10002', mode_transport='maritime', statut='livre', cout_final=Decimal('3000'))
        autre = Colis.objects.create(client=self.client_user, code_suivi='FM-DEL10003', mode_transport='maritime', statut='livre', cout_final=Decimal('2000'))
        Paiement.objects.create(client=self.client_user, colis=autre, montant='2000', methode='mtn_momo', moment='en_ligne', statut='en_attente')
        obstacles = obstacles_suppression(self.client_user)
        self.assertEqual(len(obstacles), 3)
        self.assertTrue(any('en cours' in o for o in obstacles))
        self.assertTrue(any('en attente' in o for o in obstacles))
        self.assertTrue(any('non réglé' in o for o in obstacles))

    def test_un_colis_livre_et_paye_ne_bloque_pas(self):
        historique_termine(self.client_user)
        self.assertEqual(obstacles_suppression(self.client_user), [])

    def test_un_colis_livre_gratuit_ou_sans_cout_ne_bloque_pas(self):
        Colis.objects.create(client=self.client_user, code_suivi='FM-DEL10004', mode_transport='maritime', statut='livre')
        self.assertEqual(obstacles_suppression(self.client_user), [])


class AnonymisationTests(BaseSuppression):
    def test_les_donnees_d_identification_sont_effacees_les_justificatifs_conserves(self):
        colis, paiement = historique_termine(self.client_user)
        reclamation = Reclamation.objects.create(client=self.client_user, colis=colis, type='retard', description='Retard')
        MessageReclamation.objects.create(reclamation=reclamation, expediteur=self.client_user, message='Bonjour')
        Notification.objects.create(utilisateur=self.client_user, type='general', canal='push', contenu='Salut')
        Notification.objects.create(utilisateur=self.admin, type='reclamation', canal='push', colis=colis,
                                    contenu='Nouvelle réclamation de Awa Ngo : Retard')
        FCMToken.objects.create(utilisateur=self.client_user, token='tok-1')
        CodeOtp.objects.create(utilisateur=self.client_user, code='123456', expire_le=timezone.now() + timedelta(minutes=5))
        JournalActivite.objects.create(auteur=self.admin, action='suspension_compte', cible_description=f'Awa Ngo ({TEL})')

        anonymiser_client(self.client_user)

        c = Client.objects.get(pk=self.client_user.pk)
        self.assertEqual(c.nom, LIBELLE_SUPPRIME)
        self.assertEqual(str(c.telephone), f'SUPPRIME-{c.pk}')
        self.assertEqual(c.code_client, f'SUPPRIME-{c.pk}')
        self.assertIsNone(c.email)
        self.assertFalse(c.has_usable_password())
        self.assertEqual(c.statut, 'desactive')
        self.assertIsNotNone(c.anonymise_le)
        # effacés
        self.assertFalse(Notification.objects.filter(utilisateur=c).exists())
        self.assertFalse(FCMToken.objects.filter(utilisateur=c).exists())
        self.assertFalse(CodeOtp.objects.filter(utilisateur=c).exists())
        paiement.refresh_from_db()
        self.assertIsNone(paiement.telephone_paiement)
        self.assertEqual(paiement.reference_prestataire, 'REF-1')                       # la comptabilité garde sa référence
        # traces dans les textes du personnel
        self.assertEqual(Notification.objects.get(utilisateur=self.admin).contenu, f'Nouvelle réclamation de {LIBELLE_SUPPRIME} : Retard')
        self.assertEqual(JournalActivite.objects.get().cible_description, LIBELLE_SUPPRIME)
        # conservés, rattachés au compte anonyme
        self.assertEqual(Colis.objects.get(pk=colis.pk).client_id, c.pk)
        self.assertEqual(Paiement.objects.get(pk=paiement.pk).client_id, c.pk)
        self.assertTrue(Reclamation.objects.filter(pk=reclamation.pk).exists())
        self.assertTrue(MessageReclamation.objects.filter(reclamation=reclamation).exists())

    def test_le_numero_est_libere_et_la_personne_peut_se_reinscrire(self):
        historique_termine(self.client_user)
        anonymiser_client(self.client_user)
        r = APIClient().post('/api/auth/register/', {'accepte_conditions': True, 'nom': 'Awa Retour', 'telephone': TEL, 'password': MDP}, format='json')
        self.assertEqual(r.status_code, 201, r.data)
        self.assertEqual(Client.objects.filter(telephone=TEL).count(), 1)

    def test_impossible_de_se_connecter_apres_suppression(self):
        historique_termine(self.client_user)
        anonymiser_client(self.client_user)
        r = APIClient().post('/api/auth/login/', {'telephone': TEL, 'password': MDP, 'portail': 'client'}, format='json')
        self.assertIn(r.status_code, (400, 401, 403))
        self.assertNotIn('access_token', r.cookies)

    def test_les_sessions_ouvertes_sont_fermees(self):
        historique_termine(self.client_user)
        session = self.connecter()
        self.assertEqual(session.get('/api/auth/me/').status_code, 200)
        anonymiser_client(self.client_user)
        self.assertEqual(session.get('/api/auth/me/').status_code, 401)
        self.assertEqual(session.post('/api/auth/login/refresh/').status_code, 401)

    def test_refus_tant_qu_un_colis_est_en_cours_et_rien_n_est_modifie(self):
        Colis.objects.create(client=self.client_user, code_suivi='FM-DEL20001', mode_transport='maritime', statut='en_transit')
        with self.assertRaises(SuppressionImpossible):
            anonymiser_client(self.client_user)
        c = Client.objects.get(pk=self.client_user.pk)
        self.assertEqual((c.nom, str(c.telephone), c.anonymise_le), ('Awa Ngo', TEL, None))

    def test_les_colis_d_un_compte_supprime_restent_lisibles_par_le_personnel(self):
        colis, _ = historique_termine(self.client_user)
        agent = Agent.objects.create(nom='Agent', telephone='+237677810098', localisation='cameroun', cree_par=self.admin)
        anonymiser_client(self.client_user)
        api = APIClient(); api.force_authenticate(user=agent)
        r = api.get(f'/api/colis/{colis.pk}/')
        self.assertEqual(r.status_code, 200, r.data)
        self.assertEqual(r.data['client_nom'], LIBELLE_SUPPRIME)
        self.assertEqual(api.get('/api/colis/?search=Awa').data['count'], 0)           # l'ancien nom n'est plus retrouvable
        self.assertEqual(api.get('/api/colis/?search=677810001').data['count'], 0)


class SuppressionDepuisLApplicationTests(BaseSuppression):
    def test_statut_possible_ou_non(self):
        api = self.connecter()
        r = api.get('/api/auth/me/deletion-status/')
        self.assertEqual((r.status_code, r.data), (200, {'possible': True, 'obstacles': []}))
        Colis.objects.create(client=self.client_user, code_suivi='FM-DEL30001', mode_transport='maritime')
        r = api.get('/api/auth/me/deletion-status/')
        self.assertFalse(r.data['possible'])
        self.assertIn('1 colis en cours', r.data['obstacles'][0])

    def test_suppression_avec_le_bon_mot_de_passe(self):
        historique_termine(self.client_user)
        api = self.connecter()
        r = api.post('/api/auth/me/delete/', {'password': MDP}, format='json')
        self.assertEqual(r.status_code, 200, r.data)
        self.assertEqual(r.cookies['access_token'].value, '')                          # cookies effacés dans la réponse
        self.assertEqual(Client.objects.get(pk=self.client_user.pk).nom, LIBELLE_SUPPRIME)

    def test_mauvais_mot_de_passe_rien_n_est_supprime(self):
        api = self.connecter()
        r = api.post('/api/auth/me/delete/', {'password': 'faux'}, format='json')
        self.assertEqual((r.status_code, r.data['code']), (400, 'bad_password'))
        self.assertEqual(Client.objects.get(pk=self.client_user.pk).nom, 'Awa Ngo')
        self.assertEqual(api.post('/api/auth/me/delete/', {}, format='json').status_code, 400)

    def test_refus_clair_avec_la_liste_des_obstacles(self):
        Colis.objects.create(client=self.client_user, code_suivi='FM-DEL30002', mode_transport='maritime', statut='embarque')
        api = self.connecter()
        r = api.post('/api/auth/me/delete/', {'password': MDP}, format='json')
        self.assertEqual((r.status_code, r.data['code']), (409, 'suppression_impossible'))
        self.assertEqual(len(r.data['obstacles']), 1)
        self.assertEqual(Client.objects.get(pk=self.client_user.pk).nom, 'Awa Ngo')

    def test_reserve_aux_clients_connectes(self):
        self.assertEqual(APIClient().post('/api/auth/me/delete/', {'password': MDP}, format='json').status_code, 401)
        agent_api = APIClient(); agent_api.force_authenticate(user=self.admin)
        self.assertEqual(agent_api.post('/api/auth/me/delete/', {'password': MDP}, format='json').status_code, 403)
        self.assertEqual(agent_api.get('/api/auth/me/deletion-status/').status_code, 403)

    def test_limitation_des_essais_de_mot_de_passe(self):
        api = self.connecter()
        codes = [api.post('/api/auth/me/delete/', {'password': 'faux'}, format='json').status_code for _ in range(7)]
        self.assertEqual(codes[:5], [400] * 5)
        self.assertEqual(codes[5], 429)                                                  # 5 tentatives par heure


class SuppressionParCodeWebTests(BaseSuppression):
    def demander(self, tel=TEL):
        return APIClient().post('/api/auth/delete-account/request-code/', {'telephone': tel, 'canal': 'sms'}, format='json')

    def confirmer(self, code, tel=TEL):
        return APIClient().post('/api/auth/delete-account/confirm/', {'telephone': tel, 'code': code}, format='json')

    def test_un_numero_inconnu_ne_revele_rien_et_n_envoie_rien(self):
        r = self.demander('+237677819999')
        self.assertEqual(r.status_code, 200)
        self.assertEqual(CodeOtp.objects.count(), 0)

    def test_le_personnel_ne_passe_pas_par_cette_voie(self):
        self.assertEqual(self.demander('+237677810099').status_code, 200)
        self.assertEqual(CodeOtp.objects.filter(utilisateur=self.admin).count(), 0)

    def test_parcours_complet(self):
        historique_termine(self.client_user)
        self.assertEqual(self.demander().status_code, 200)
        code = CodeOtp.objects.get(utilisateur=self.client_user).code
        r = self.confirmer(code)
        self.assertEqual(r.status_code, 200, r.data)
        c = Client.objects.get(pk=self.client_user.pk)
        self.assertEqual((c.nom, c.statut), (LIBELLE_SUPPRIME, 'desactive'))
        self.assertEqual(self.confirmer(code).status_code, 400)                          # le code ne sert qu'une fois

    def test_mauvais_code_compte_les_essais_puis_bloque(self):
        self.demander()
        vrai = CodeOtp.objects.get(utilisateur=self.client_user).code
        faux = '000000' if vrai != '000000' else '111111'
        for _ in range(5):
            self.assertEqual(self.confirmer(faux).status_code, 400)
        self.assertEqual(self.confirmer(vrai).status_code, 429)
        self.assertEqual(Client.objects.get(pk=self.client_user.pk).nom, 'Awa Ngo')

    def test_code_expire(self):
        self.demander()
        CodeOtp.objects.update(expire_le=timezone.now() - timedelta(seconds=1))
        code = CodeOtp.objects.get().code
        self.assertEqual(self.confirmer(code).status_code, 400)

    def test_obstacle_avertit_sans_consommer_le_code(self):
        Colis.objects.create(client=self.client_user, code_suivi='FM-DEL40001', mode_transport='maritime', statut='embarque')
        self.demander()
        code = CodeOtp.objects.get().code
        r = self.confirmer(code)
        self.assertEqual((r.status_code, r.data['code']), (409, 'suppression_impossible'))
        self.assertTrue(CodeOtp.objects.filter(code=code, verifie_le__isnull=True).exists())     # réutilisable après résolution
        self.assertEqual(Client.objects.get(pk=self.client_user.pk).nom, 'Awa Ngo')

    def test_un_numero_inconnu_ou_un_code_au_hasard_ne_supprime_rien(self):
        self.assertEqual(self.confirmer('123456', '+237677819999').status_code, 400)
        self.assertEqual(self.confirmer('123456').status_code, 400)

    def test_demandes_de_code_limitees(self):
        self.assertEqual(self.demander().status_code, 200)
        self.assertEqual(self.demander().status_code, 429)                               # délai de 60 s entre deux codes


class SuppressionParAdministrateurTests(BaseSuppression):
    def supprimer(self, client):
        api = APIClient(); api.force_authenticate(user=self.admin)
        return api.delete(f'/api/admin/users/{client.pk}/')

    def test_client_avec_historique_termine_est_anonymise_au_lieu_d_etre_refuse(self):
        colis, _ = historique_termine(self.client_user)
        r = self.supprimer(self.client_user)
        self.assertEqual(r.status_code, 200, r.data)
        self.assertIn('anonymisé', r.data['detail'])
        self.assertEqual(Client.objects.get(pk=self.client_user.pk).nom, LIBELLE_SUPPRIME)
        self.assertTrue(Colis.objects.filter(pk=colis.pk).exists())

    def test_le_journal_ne_garde_aucune_donnee_personnelle(self):
        historique_termine(self.client_user)
        self.supprimer(self.client_user)
        entree = JournalActivite.objects.get(action='suppression_compte')
        self.assertEqual(entree.cible_description, f'Client #{self.client_user.pk}')
        self.assertNotIn('Awa', entree.cible_description)
        self.assertNotIn('677810001', entree.cible_description)

    def test_client_sans_aucun_historique_est_supprime_de_la_base(self):
        r = self.supprimer(self.client_user)
        self.assertEqual(r.status_code, 200)
        self.assertFalse(Client.objects.filter(pk=self.client_user.pk).exists())

    def test_client_avec_colis_en_cours_est_refuse_avec_la_liste(self):
        Colis.objects.create(client=self.client_user, code_suivi='FM-DEL50001', mode_transport='maritime', statut='recu_entrepot')
        r = self.supprimer(self.client_user)
        self.assertEqual(r.status_code, 400)
        self.assertEqual(len(r.data['obstacles']), 1)

    def test_les_comptes_supprimes_disparaissent_de_la_liste_et_ne_se_reactivent_pas(self):
        historique_termine(self.client_user)
        self.supprimer(self.client_user)
        api = APIClient(); api.force_authenticate(user=self.admin)
        self.assertEqual(api.get('/api/users/?role=client').data['count'], 0)
        r = api.post(f'/api/admin/users/{self.client_user.pk}/statut/', {'statut': 'actif'}, format='json')
        self.assertEqual(r.status_code, 400)
        self.assertIn('supprimé', r.data['detail'])
