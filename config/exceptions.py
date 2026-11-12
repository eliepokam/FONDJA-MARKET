"""
Gestion des erreurs de l'API : toujours en JSON, toujours en FRANÇAIS.

Pourquoi un dictionnaire : même avec LANGUAGE_CODE = 'fr', certaines bibliothèques (simplejwt, Django) laissent passer des messages anglais
(« No active account found with the given credentials », « No Colis matches the given query »…). On les traduit ici, en conservant le
`code` technique (`token_not_valid`, `no_active_account`…) sur lequel le front s'appuie pour sa logique.
"""
import logging
import re

from rest_framework import status
from rest_framework.exceptions import ErrorDetail
from rest_framework.response import Response
from rest_framework.views import exception_handler

logger = logging.getLogger('django')

# Message anglais exact -> français
MESSAGES = {
    "Given token not valid for any token type": "Votre session est invalide ou a expiré. Reconnectez-vous.",
    "Token is invalid or expired": "Votre session est invalide ou a expiré. Reconnectez-vous.",
    "Token is invalid": "Votre session est invalide. Reconnectez-vous.",
    "Token is expired": "Votre session a expiré. Reconnectez-vous.",
    "Token has wrong type": "Votre session est invalide. Reconnectez-vous.",
    "Token contained no recognizable user identification": "Votre session est invalide. Reconnectez-vous.",
    "Invalid token.": "Votre session est invalide. Reconnectez-vous.",
    "Invalid token header. No credentials provided.": "Votre session est invalide. Reconnectez-vous.",
    "No active account found with the given credentials": "Numéro de téléphone ou mot de passe incorrect.",
    "Unable to log in with provided credentials.": "Numéro de téléphone ou mot de passe incorrect.",
    "User not found": "Compte introuvable.",
    "User is inactive": "Ce compte est désactivé.",
    "Authentication credentials were not provided.": "Connexion requise : veuillez vous connecter.",
    "Incorrect authentication credentials.": "Identifiants incorrects.",
    "You do not have permission to perform this action.": "Vous n'avez pas la permission d'effectuer cette action.",
    "Not found.": "Élément introuvable.",
    "Invalid page.": "Page inexistante.",
    "Bad Request": "Requête invalide.",
    "Forbidden": "Accès refusé.",
    "Not Found": "Élément introuvable.",
    "Server Error": "Une erreur interne est survenue. Réessayez plus tard.",
    "Unsupported media type": "Format de fichier non pris en charge.",
    "This field is required.": "Ce champ est obligatoire.",
    "This field may not be blank.": "Ce champ ne peut pas être vide.",
    "This field may not be null.": "Ce champ ne peut pas être vide.",
    "A valid integer is required.": "Un nombre entier valide est requis.",
    "A valid number is required.": "Un nombre valide est requis.",
    "Enter a valid email address.": "Saisissez une adresse e-mail valide.",
    "Not a valid string.": "Texte invalide.",
    "Request was throttled.": "Trop de requêtes. Réessayez dans un instant.",
}
# Motifs : « No Colis matches the given query. », « Request was throttled. Expected available in 52 seconds. »…
MOTIFS = [
    (re.compile(r"^No \w+ matches the given query\.?$"), "Élément introuvable, ou il ne vous appartient pas."),
    (re.compile(r"^Request was throttled\. Expected available in (\d+) seconds?\.?$"), r"Trop de requêtes. Réessayez dans \1 seconde(s)."),
    (re.compile(r"^Ensure this field has no more than (\d+) characters\.?$"), r"Ce champ ne peut pas dépasser \1 caractères."),
    (re.compile(r"^Ensure this field has at least (\d+) characters\.?$"), r"Ce champ doit contenir au moins \1 caractères."),
    (re.compile(r"^\"(.+)\" is not a valid choice\.?$"), r"« \1 » n'est pas un choix valide."),
]


def traduire_message(texte):
    """Français pour un message connu ; le texte d'origine sinon (il est déjà en français dans la quasi-totalité des cas)."""
    if texte in MESSAGES:
        return MESSAGES[texte]
    for motif, remplacement in MOTIFS:
        if motif.match(texte):
            return motif.sub(remplacement, texte)
    return texte


def traduire(donnees):
    """Parcourt le corps d'une erreur (dict / liste / texte) et traduit ce qui doit l'être, sans perdre le `code` des ErrorDetail."""
    if isinstance(donnees, ErrorDetail):
        return ErrorDetail(traduire_message(str(donnees)), code=donnees.code)
    if isinstance(donnees, str):
        return traduire_message(donnees)
    if isinstance(donnees, dict):
        return {cle: traduire(valeur) for cle, valeur in donnees.items()}
    if isinstance(donnees, (list, tuple)):
        return [traduire(element) for element in donnees]
    return donnees


def custom_exception_handler(exc, context):
    response = exception_handler(exc, context)

    if response is not None:
        # DRF a su gérer l'exception (validation, 404, permission...) : on se contente de traduire ce qui serait resté en anglais.
        response.data = traduire(response.data)
        return response

    # Exception imprévue (bug, erreur DB...) : on logge la vraie trace pour nous,
    # on ne renvoie JAMAIS ça au client.
    logger.error(f"Erreur non gérée sur {context['request'].path}", exc_info=exc)
    return Response(
        {'detail': "Une erreur interne est survenue. Réessayez plus tard."},
        status=status.HTTP_500_INTERNAL_SERVER_ERROR
    )
