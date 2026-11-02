import logging

import firebase_admin
from firebase_admin import credentials
from django.conf import settings

logger = logging.getLogger('django')

# Initialisation tolérante : sans fichier d'identifiants valide, l'application démarre quand même
# (les notifications push sont simplement désactivées) au lieu de planter au lancement de n'importe
# quelle commande Django. Idempotente : un second import ne recrée pas l'application Firebase.
if not firebase_admin._apps:
    try:
        firebase_admin.initialize_app(credentials.Certificate(settings.FIREBASE_CREDENTIALS_PATH))
    except Exception as exc:  # fichier absent, JSON invalide...
        logger.warning(f"Firebase non initialisé : notifications push désactivées ({exc})")
