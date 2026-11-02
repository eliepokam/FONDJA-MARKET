import io
from unittest.mock import patch

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import SimpleTestCase
from PIL import Image
from rest_framework.exceptions import ValidationError

from core.validators import valider_image


def fichier(format_image='PNG', nom='x.png'):
    tampon = io.BytesIO()
    Image.new('RGB', (5, 5), 'red').save(tampon, format=format_image)
    return SimpleUploadedFile(nom, tampon.getvalue())


class ValiderImageTests(SimpleTestCase):
    def test_images_acceptees(self):
        for fmt in ('PNG', 'JPEG', 'GIF', 'WEBP'):
            f = fichier(fmt, f'x.{fmt.lower()}')
            self.assertIs(valider_image(f), f)
            self.assertEqual(f.tell(), 0)                              # le fichier reste lisible pour l'enregistrement

    def test_texte_et_html_refuses(self):
        for contenu in (b'hello', b'<html><script>alert(1)</script></html>', b''):
            with self.assertRaises(ValidationError):
                valider_image(SimpleUploadedFile('a.png', contenu))

    def test_format_non_autorise_refuse(self):
        with self.assertRaises(ValidationError):
            valider_image(fichier('BMP', 'x.bmp'))

    def test_taille_maximale(self):
        with self.assertRaises(ValidationError):
            valider_image(SimpleUploadedFile('big.png', b'0' * (9 * 1024 * 1024)))

    def test_message_par_champ_dans_une_vue(self):
        with self.assertRaises(ValidationError) as ctx:
            valider_image(SimpleUploadedFile('a.png', b'nope'), 'photo')
        self.assertIn('photo', ctx.exception.detail)


class FirebaseTolerantTests(SimpleTestCase):
    def test_l_application_demarre_meme_sans_identifiants_firebase(self):
        import importlib
        import firebase_admin
        from core import firebase as module_firebase
        with patch.object(firebase_admin, '_apps', {}), \
             patch('firebase_admin.credentials.Certificate', side_effect=FileNotFoundError('absent')):
            importlib.reload(module_firebase)                           # ne doit pas lever
