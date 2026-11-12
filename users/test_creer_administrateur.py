"""Création du premier administrateur en ligne de commande."""
from io import StringIO
from unittest.mock import patch

from django.core.management import call_command
from django.core.management.base import CommandError
from django.core.cache import cache
from django.utils import timezone
from rest_framework.test import APITestCase, APIClient

from users.models import Administrateur, Agent, Client, Utilisateur

MDP = 'Zebre!Orange2026'
TEL = '+237656645649'


class CreerAdministrateurTests(APITestCase):
    def setUp(self):
        cache.clear()

    def lancer(self, *args, **kw):
        sortie = StringIO()
        call_command('creer_administrateur', *args, stdout=sortie, **kw)
        return sortie.getvalue()

    def connexion(self, tel=TEL, mdp=MDP, portail='staff'):
        return APIClient().post('/api/auth/login/', {'telephone': tel, 'password': mdp, 'portail': portail}, format='json')

    def test_creation_complete_et_connexion_au_portail_du_personnel(self):
        sortie = self.lancer('--nom=Morel', f'--telephone={TEL}', f'--password={MDP}')
        a = Administrateur.objects.get(telephone=TEL)
        self.assertEqual((a.nom, a.statut), ('Morel', 'actif'))
        self.assertTrue(a.is_superuser and a.is_staff)
        self.assertIsNotNone(a.telephone_verifie_le)                                  # pas de code SMS à valider
        self.assertTrue(a.check_password(MDP))
        self.assertIn('/staff-portal/login', sortie)
        r = self.connexion()
        self.assertEqual(r.status_code, 200, r.data)
        self.assertIn('access_token', r.cookies)
        self.assertEqual(APIClient().get('/api/auth/me/').status_code, 401)           # (sans cookie, pas de session)

    def test_le_portail_client_reste_ferme_a_un_administrateur(self):
        self.lancer('--nom=Morel', f'--telephone={TEL}', f'--password={MDP}')
        self.assertEqual(self.connexion(portail='client').status_code, 403)

    def test_formats_de_numero_acceptes_et_normalises(self):
        for saisie in ('656645649', '656 64 56 49', '+237 656 64 56 49', '00237656645649'):
            Utilisateur.objects.all().delete()
            self.lancer('--nom=Morel', f'--telephone={saisie}', f'--password={MDP}')
            self.assertEqual(str(Administrateur.objects.get().telephone), TEL, saisie)

    def test_numero_invalide_refuse_sans_rien_creer(self):
        with self.assertRaisesMessage(CommandError, 'invalide'):
            self.lancer('--nom=Morel', '--telephone=123', f'--password={MDP}')
        self.assertFalse(Utilisateur.objects.exists())

    def test_mot_de_passe_trop_faible_refuse(self):
        with self.assertRaisesMessage(CommandError, 'Mot de passe refusé'):
            self.lancer('--nom=Morel', f'--telephone={TEL}', '--password=12345678')
        self.assertFalse(Utilisateur.objects.exists())

    def test_saisie_interactive_du_mot_de_passe_jamais_dans_la_ligne_de_commande(self):
        with patch('sys.stdin.isatty', return_value=True), patch('getpass.getpass', side_effect=[MDP, MDP]):
            self.lancer('--nom=Morel', f'--telephone={TEL}')
        self.assertTrue(Administrateur.objects.get(telephone=TEL).check_password(MDP))

    def test_deux_mots_de_passe_differents_refuses(self):
        with patch('sys.stdin.isatty', return_value=True), patch('getpass.getpass', side_effect=[MDP, 'Autre!Mdp2026x']):
            with self.assertRaisesMessage(CommandError, 'ne correspondent pas'):
                self.lancer('--nom=Morel', f'--telephone={TEL}')
        self.assertFalse(Utilisateur.objects.exists())

    def test_questions_posees_si_nom_et_telephone_absents(self):
        with patch('sys.stdin.isatty', return_value=True), patch('builtins.input', side_effect=['Morel', '656 64 56 49']), \
                patch('getpass.getpass', side_effect=[MDP, MDP]):
            self.lancer()
        self.assertEqual(Administrateur.objects.get().nom, 'Morel')

    def test_non_interactif_sans_mot_de_passe_echoue_clairement(self):
        with patch('sys.stdin.isatty', return_value=False):
            with self.assertRaisesMessage(CommandError, 'Mot de passe requis'):
                self.lancer('--nom=Morel', f'--telephone={TEL}')

    def test_sans_django_admin(self):
        self.lancer('--nom=Morel', f'--telephone={TEL}', f'--password={MDP}', '--sans-django-admin')
        a = Administrateur.objects.get()
        self.assertFalse(a.is_superuser)
        self.assertTrue(a.is_staff)                                                    # reste administrateur de l'application
        self.assertEqual(self.connexion().status_code, 200)

    def test_deja_administrateur_ne_change_rien_sans_reinitialiser(self):
        self.lancer('--nom=Morel', f'--telephone={TEL}', f'--password={MDP}')
        sortie = self.lancer('--nom=Morel', f'--telephone={TEL}', '--password=Nouveau!Mdp2026x')
        self.assertIn('--reinitialiser', sortie)
        self.assertTrue(Administrateur.objects.get().check_password(MDP))

    def test_reinitialiser_change_le_mot_de_passe_et_debloque_le_compte(self):
        self.lancer('--nom=Morel', f'--telephone={TEL}', f'--password={MDP}')
        Administrateur.objects.update(tentatives_connexion_echouees=9, bloque_jusqu_a=timezone.now() + timezone.timedelta(hours=1), statut='desactive')
        self.lancer('--nom=Morel', f'--telephone={TEL}', '--password=Nouveau!Mdp2026x', '--reinitialiser')
        a = Administrateur.objects.get()
        self.assertEqual((a.statut, a.tentatives_connexion_echouees, a.bloque_jusqu_a), ('actif', 0, None))
        self.assertEqual(self.connexion(mdp=MDP).status_code, 401)
        self.assertEqual(self.connexion(mdp='Nouveau!Mdp2026x').status_code, 200)

    def test_un_numero_client_ou_agent_est_refuse_sans_etre_modifie(self):
        Client.objects.create(nom='Awa', telephone='+237677000001', code_client='CL-AD01')
        with self.assertRaisesMessage(CommandError, 'compte client'):
            self.lancer('--nom=X', '--telephone=+237677000001', f'--password={MDP}')
        Agent.objects.create(nom='Ag', telephone='+237677000002')
        with self.assertRaisesMessage(CommandError, 'compte agent'):
            self.lancer('--nom=X', '--telephone=+237677000002', f'--password={MDP}')
        self.assertFalse(Administrateur.objects.exists())

    def test_un_superuser_cree_avec_createsuperuser_est_promu_administrateur(self):
        """Cas du « createsuperuser » : l'utilisateur existe mais le portail du personnel le refuse (aucun rôle)."""
        nu = Utilisateur.objects.create_superuser(telephone=TEL, nom='Morel', password=MDP)
        self.assertEqual(self.connexion().status_code, 403)                            # avant : « réservé au personnel »
        self.lancer('--nom=Morel', f'--telephone={TEL}')                               # mot de passe conservé
        self.assertTrue(Administrateur.objects.filter(pk=nu.pk).exists())
        self.assertEqual(Utilisateur.objects.filter(telephone=TEL).count(), 1)         # même compte, pas un doublon
        r = self.connexion()
        self.assertEqual(r.status_code, 200, r.data)

    def test_promotion_avec_nouveau_mot_de_passe(self):
        Utilisateur.objects.create_superuser(telephone=TEL, nom='Morel', password='Ancien!Mdp2026x')
        self.lancer('--nom=Morel', f'--telephone={TEL}', f'--password={MDP}')
        self.assertEqual(self.connexion(mdp='Ancien!Mdp2026x').status_code, 401)
        self.assertEqual(self.connexion().status_code, 200)

    def test_nom_ou_telephone_manquants(self):
        with patch('sys.stdin.isatty', return_value=False):
            with self.assertRaisesMessage(CommandError, 'obligatoires'):
                self.lancer(f'--password={MDP}')
