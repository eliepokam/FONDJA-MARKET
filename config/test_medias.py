"""Service des médias (/media/) indépendant de DEBUG, pour un hébergement de test public."""
import importlib
import os
import tempfile

from django.test import SimpleTestCase, override_settings
from django.urls import clear_url_caches


def recharger_routes():
    import config.urls
    importlib.reload(config.urls)
    clear_url_caches()


class ServiceDesMediasTests(SimpleTestCase):
    def setUp(self):
        self.dossier = tempfile.TemporaryDirectory()
        with open(os.path.join(self.dossier.name, 'preuve.txt'), 'w') as f:
            f.write('contenu de la preuve')
        self.addCleanup(self.dossier.cleanup)
        self.addCleanup(recharger_routes)          # remet les routes d'origine après chaque test

    def lire(self, chemin='/media/preuve.txt'):
        recharger_routes()
        return self.client.get(chemin)

    def test_debug_faux_sans_reglage_les_medias_ne_sont_pas_servis(self):
        with override_settings(DEBUG=False, SERVE_MEDIA=False, MEDIA_ROOT=self.dossier.name):
            self.assertEqual(self.lire().status_code, 404)

    def test_debug_faux_avec_serve_media_les_medias_sont_servis(self):
        with override_settings(DEBUG=False, SERVE_MEDIA=True, MEDIA_ROOT=self.dossier.name):
            r = self.lire()
            self.assertEqual(r.status_code, 200)
            self.assertEqual(b''.join(r.streaming_content), b'contenu de la preuve')

    def test_debug_vrai_les_medias_restent_servis_comme_avant(self):
        with override_settings(DEBUG=True, MEDIA_ROOT=self.dossier.name):
            self.assertEqual(self.lire().status_code, 200)

    def test_fichier_inexistant_et_remontee_de_dossier_refuses(self):
        with override_settings(DEBUG=False, SERVE_MEDIA=True, MEDIA_ROOT=self.dossier.name):
            self.assertEqual(self.lire('/media/absent.txt').status_code, 404)
            for tentative in ('/media/../settings.py', '/media/..%2f..%2fetc%2fpasswd', '/media/%2e%2e/%2e%2e/manage.py'):
                self.assertIn(self.lire(tentative).status_code, (301, 400, 404), tentative)
