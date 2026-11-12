"""Tous les messages d'erreur de l'API sont en français, et le `code` technique est conservé."""
import re

from django.core.cache import cache
from django.test import SimpleTestCase, override_settings
from django.utils import timezone, translation
from rest_framework.exceptions import ErrorDetail
from rest_framework.test import APITestCase, APIClient

from config.exceptions import traduire, traduire_message
from users.models import Client

MDP = 'Zebre!Orange2026'
# Mots anglais courants : s'il en reste dans un message, c'est qu'une traduction manque
ANGLAIS = re.compile(r"\b(the|with|given|credentials|found|matches|invalid|required|was|query|expected|account|please)\b", re.I)


def texte(donnees):
    """Tous les messages d'un corps d'erreur, à plat."""
    if isinstance(donnees, dict):
        return ' '.join(texte(v) for k, v in donnees.items() if k != 'code')
    if isinstance(donnees, list):
        return ' '.join(texte(v) for v in donnees)
    return str(donnees)


class TraductionTests(SimpleTestCase):
    def test_messages_connus(self):
        self.assertEqual(traduire_message("No active account found with the given credentials"), "Numéro de téléphone ou mot de passe incorrect.")
        self.assertIn("session", traduire_message("Given token not valid for any token type"))
        self.assertEqual(traduire_message("Not found."), "Élément introuvable.")

    def test_motifs(self):
        self.assertIn("introuvable", traduire_message("No Colis matches the given query."))
        self.assertIn("introuvable", traduire_message("No Reclamation matches the given query."))
        self.assertEqual(traduire_message("Request was throttled. Expected available in 52 seconds."), "Trop de requêtes. Réessayez dans 52 seconde(s).")
        self.assertEqual(traduire_message('"bateau" is not a valid choice.'), "« bateau » n'est pas un choix valide.")
        self.assertEqual(traduire_message("Ensure this field has at least 8 characters."), "Ce champ doit contenir au moins 8 caractères.")

    def test_un_texte_deja_francais_n_est_pas_modifie(self):
        self.assertEqual(traduire_message("Ce colis a déjà été pris en charge."), "Ce colis a déjà été pris en charge.")

    def test_structure_et_code_conserves(self):
        entree = {'detail': ErrorDetail("Given token not valid for any token type", code='token_not_valid'),
                  'code': ErrorDetail('token_not_valid', code='token_not_valid'),
                  'champ': [ErrorDetail("This field is required.", code='required')],
                  'imbrique': {'x': "No Colis matches the given query."}}
        sortie = traduire(entree)
        self.assertEqual(sortie['detail'].code, 'token_not_valid')                      # le front s'appuie sur ce code
        self.assertEqual(str(sortie['code']), 'token_not_valid')                         # le champ « code » n'est jamais traduit
        self.assertEqual(str(sortie['champ'][0]), "Ce champ est obligatoire.")
        self.assertIn("introuvable", sortie['imbrique']['x'])


class ReponsesDeLApiTests(APITestCase):
    def setUp(self):
        cache.clear()
        self.client_user = Client.objects.create(nom='Awa', telephone='+237677995001', code_client='CL-FR02', telephone_verifie_le=timezone.now())
        self.client_user.set_password(MDP); self.client_user.save()
        self.auth = APIClient(); self.auth.force_authenticate(user=self.client_user)

    def verifier(self, reponse, statut, contient=None):
        self.assertEqual(reponse.status_code, statut, getattr(reponse, 'data', reponse.content[:100]))
        corps = texte(reponse.json())
        self.assertFalse(ANGLAIS.search(corps), f"reste de l'anglais : {corps!r}")
        if contient:
            self.assertIn(contient, corps)
        return reponse

    def test_connexion_mauvais_mot_de_passe(self):
        r = APIClient().post('/api/auth/login/', {'telephone': '+237677995001', 'password': 'faux', 'portail': 'client'}, format='json')
        self.verifier(r, 401, "mot de passe incorrect")
        self.assertEqual(r.json()['detail'], "Numéro de téléphone ou mot de passe incorrect.")

    def test_connexion_compte_inconnu(self):
        r = APIClient().post('/api/auth/login/', {'telephone': '+237600000099', 'password': 'x', 'portail': 'client'}, format='json')
        self.verifier(r, 401, "mot de passe incorrect")

    def test_jeton_invalide_en_conservant_le_code_technique(self):
        r = APIClient(HTTP_AUTHORIZATION='Bearer abc').get('/api/colis/')
        self.verifier(r, 401, "session")
        self.assertEqual(r.json().get('code'), 'token_not_valid')

    def test_non_connecte(self):
        self.verifier(APIClient().get('/api/colis/'), 401)

    def test_colis_inexistant(self):
        r = self.verifier(self.auth.get('/api/colis/99999/'), 404, "introuvable")
        self.assertNotIn('matches', r.content.decode())

    def test_route_inexistante_renvoie_du_json_francais(self):
        r = APIClient().get('/api/cette-route-nexiste-pas/')
        self.assertEqual(r.status_code, 404)
        self.assertEqual(r['Content-Type'], 'application/json')
        self.assertEqual(r.json()['detail'], "Page ou élément introuvable.")

    def test_page_inexistante_hors_api_en_html_francais(self):
        r = APIClient().get('/page-inexistante/')
        self.assertEqual(r.status_code, 404)
        self.assertIn("lang='fr'", r.content.decode())
        self.assertIn("Retour à l'accueil", r.content.decode())
        self.assertNotIn("Not Found", r.content.decode())

    def test_acces_interdit_et_methode_non_autorisee(self):
        self.verifier(self.auth.get('/api/admin/stats/'), 403, "permission")
        self.verifier(self.auth.put('/api/auth/me/', {}, format='json'), 405)

    def test_validation_champs(self):
        self.verifier(APIClient().post('/api/auth/login/', {}, format='json'), 400, "obligatoire")
        self.verifier(self.auth.post('/api/colis/', {'mode_transport': 'bateau-volant'}, format='json'), 400, "choix valide")

    @override_settings(LANGUAGE_CODE='en-us')
    def test_meme_avec_un_mauvais_reglage_de_langue_le_middleware_force_le_francais(self):
        from django.test import override_settings as os_
        with os_(MIDDLEWARE=['config.middleware.FrancaisMiddleware', *__import__('django.conf', fromlist=['settings']).settings.MIDDLEWARE]):
            r = APIClient().post('/api/auth/login/', {}, format='json')
        self.assertIn("obligatoire", texte(r.json()))

    def test_limitation_de_debit_en_francais(self):
        for _ in range(25):
            r = APIClient().post('/api/auth/otp/resend/', {'telephone': '+237677995001'}, format='json')
        self.assertEqual(r.status_code, 429)
        self.assertFalse(ANGLAIS.search(texte(r.json())), texte(r.json()))
