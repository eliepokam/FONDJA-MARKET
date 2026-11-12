from PIL import Image
from rest_framework.exceptions import ValidationError

MAX_PREUVE_OCTETS = 8 * 1024 * 1024
FORMATS_IMAGE = {'JPEG': 'jpg', 'PNG': 'png', 'WEBP': 'webp'}
TYPES_MIME = {'jpg': 'image/jpeg', 'png': 'image/png', 'webp': 'image/webp', 'pdf': 'application/pdf'}


def valider_preuve(fichier, champ='preuve'):
    """
    Une preuve de virement : photo (JPEG, PNG, WebP) ou PDF, 8 Mo maximum. Le type est vérifié sur le CONTENU du fichier
    (pas sur son nom ni sur le type annoncé par le navigateur). Retourne l'extension à utiliser (jpg, png, webp, pdf).
    """
    def refuser(message):
        raise ValidationError({champ: [message]})

    if fichier.size > MAX_PREUVE_OCTETS:
        refuser("Fichier trop lourd (8 Mo maximum).")
    debut = fichier.read(8)
    fichier.seek(0)
    if debut.startswith(b'%PDF-'):
        return 'pdf'
    try:
        image = Image.open(fichier)
        format_image = image.format
        image.verify()
    except Exception:
        refuser("Le fichier doit être une photo (JPEG, PNG, WebP) ou un document PDF.")
    finally:
        fichier.seek(0)
    if format_image not in FORMATS_IMAGE:
        refuser("Format non accepté : envoyez une photo JPEG, PNG ou WebP, ou un PDF.")
    return FORMATS_IMAGE[format_image]
