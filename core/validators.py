from PIL import Image
from rest_framework.exceptions import ValidationError

MAX_IMAGE_OCTETS = 8 * 1024 * 1024
FORMATS_AUTORISES = {'JPEG', 'PNG', 'WEBP', 'GIF'}


def _refuser(message, champ):
    # Dans une vue on renvoie {champ: message} ; dans un validateur de serializer, le message seul suffit.
    raise ValidationError({champ: message} if champ else message)


def valider_image(fichier, champ=None):
    """
    Vérifie qu'un fichier envoyé est bien une image raisonnable. À appeler partout où un fichier est
    enregistré SANS passer par un champ de serializer (ex. `Model.objects.create(url=request.FILES[...])`),
    car un ImageField ne contrôle rien dans ce cas : n'importe quel fichier serait stocké.
    """
    if fichier.size > MAX_IMAGE_OCTETS:
        _refuser("Image trop lourde (8 Mo maximum).", champ)
    try:
        image = Image.open(fichier)
        format_image = image.format
        image.verify()
    except Exception:
        _refuser("Le fichier envoyé n'est pas une image valide.", champ)
    finally:
        fichier.seek(0)
    if format_image not in FORMATS_AUTORISES:
        _refuser("Format d'image non accepté (JPEG, PNG, WebP ou GIF).", champ)
    return fichier


MAX_VIDEO_OCTETS = 100 * 1024 * 1024


def valider_video(fichier, champ=None):
    """MP4 / MOV (signature « ftyp ») ou WebM (signature EBML), 100 Mo maximum."""
    if fichier.size > MAX_VIDEO_OCTETS:
        _refuser("Vidéo trop lourde (100 Mo maximum).", champ)
    entete = fichier.read(16)
    fichier.seek(0)
    est_mp4_ou_mov = entete[4:8] == b'ftyp'
    est_webm = entete[:4] == b'\x1a\x45\xdf\xa3'
    if not (est_mp4_ou_mov or est_webm):
        _refuser("Format de vidéo non accepté (MP4, MOV ou WebM).", champ)
    return fichier
