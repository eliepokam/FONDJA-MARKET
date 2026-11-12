import logging
import os

import firebase_admin
from firebase_admin import credentials
from django.conf import settings

logger = logging.getLogger('django')

# Initialisation tolérante : sans fichier d'identifiants valide, l'application démarre quand même
# (les notifications push sont simplement désactivées) au lieu de planter au lancement de n'importe
# quelle commande Django. Idempotente : un second import ne recrée pas l'application Firebase.
# Le chemin est déjà rendu absolu dans settings.py (relatif = depuis le dossier du projet).
if not firebase_admin._apps:
    chemin = str(getattr(settings, 'FIREBASE_CREDENTIALS_PATH', '') or '')
    try:
        if not os.path.isfile(chemin):
            raise FileNotFoundError(f"fichier introuvable : {chemin or '(FIREBASE_CREDENTIALS_PATH vide)'}")
        firebase_admin.initialize_app(credentials.Certificate(chemin))
        logger.info(f"Firebase initialisé ({os.path.basename(chemin)}) : notifications push actives.")
    except Exception as exc:  # fichier absent, JSON invalide...
        logger.warning(f"Firebase non initialisé : notifications push désactivées ({exc})")
