import os

from django.conf import settings
from django.core.files.storage import FileSystemStorage


class StockagePrive(FileSystemStorage):
    """
    Stockage des preuves de paiement : un dossier SÉPARÉ de MEDIA_ROOT (réglage PRIVATE_MEDIA_ROOT, par défaut « media_prive »).
    Une preuve de virement contient des informations bancaires : elle ne doit jamais être accessible par une simple adresse /media/…,
    mais uniquement par l'API, après vérification des droits (le client concerné, un agent ou un administrateur).
    Le dossier est lu à chaque utilisation (et non à l'import) pour que les tests puissent le rediriger.
    """
    @property
    def base_location(self):
        return str(getattr(settings, 'PRIVATE_MEDIA_ROOT', None) or (settings.BASE_DIR / 'media_prive'))

    @property
    def location(self):
        return os.path.abspath(self.base_location)

    def url(self, name):
        raise ValueError("Les preuves de paiement ne sont pas servies par une adresse publique.")


def stockage_prive():
    return StockagePrive()
