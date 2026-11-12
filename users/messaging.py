"""
Envoi des codes de vérification : choix du canal, WhatsApp via Twilio, et explication des erreurs.

Réglages (variables d'environnement, tous facultatifs sauf les identifiants Twilio) :
  OTP_CANAUX                         canaux proposés, ex. « whatsapp » (SMS indisponible) ou « whatsapp,sms »
  TWILIO_ACCOUNT_SID / AUTH_TOKEN    identifiants
  TWILIO_WHATSAPP_FROM               expéditeur : +14155238886 = bac à sable Twilio ; sinon votre numéro WhatsApp validé
  TWILIO_WHATSAPP_CONTENT_SID        (HX…) modèle de message pré-approuvé. Vide = message libre (bac à sable : seulement dans les
                                     24 h qui suivent un message du testeur). Rempli = le modèle est utilisé : seul moyen d'écrire
                                     à quelqu'un qui ne vous a pas écrit récemment, et obligatoire en production.
  TWILIO_WHATSAPP_CONTENT_VARIABLES  variables du modèle en JSON, avec {code} et {service} :
                                       modèle « Your {{1}} code is {{2}} » du bac à sable →  {"1":"{service}","2":"{code}"}
                                       modèle « Authentification » à une variable        →  {"1":"{code}"}   (défaut)
  TWILIO_SANDBOX_JOIN_CODE           ex. « join bright-tiger » : affiche aux testeurs un lien d'aide pour rejoindre le bac à sable
"""
import json
import logging

from django.conf import settings

logger = logging.getLogger('django')

SERVICE = "Fondja Market"
CANAUX_CONNUS = ('whatsapp', 'sms')

# Erreurs Twilio pour lesquelles réessayer ne sert à rien : on le dit clairement dans les journaux et on arrête.
DEFINITIVES = {
    20003: "Identifiants Twilio refusés : vérifiez TWILIO_ACCOUNT_SID et TWILIO_AUTH_TOKEN.",
    21211: "Numéro destinataire invalide.",
    21408: "Twilio n'est pas autorisé à envoyer vers ce pays (Permissions géographiques de la console Twilio).",
    21608: "Compte Twilio d'essai : le numéro destinataire doit d'abord être vérifié dans la console Twilio.",
    21610: "Le destinataire s'est désabonné (STOP).",
    21614: "Ce numéro ne peut pas recevoir de messages.",
    63007: "Expéditeur WhatsApp introuvable : vérifiez TWILIO_WHATSAPP_FROM (avec le « whatsapp: » géré automatiquement).",
    63015: "Le destinataire n'a PAS rejoint le bac à sable WhatsApp (ou sa connexion de 3 jours a expiré) : il doit envoyer « join <votre code> » au numéro du bac à sable.",
    63016: "Message libre refusé : hors des 24 h qui suivent un message du destinataire. Renseignez TWILIO_WHATSAPP_CONTENT_SID (modèle) ou demandez au destinataire de renvoyer « join … ».",
    63018: "Limite d'envoi WhatsApp atteinte pour ce destinataire.",
    63038: "Plafond QUOTIDIEN de messages du compte Twilio atteint (compte d'essai : fenêtre de 24 h glissante, parfois plus basse que 50). Attendez que les anciens messages sortent de la fenêtre, passez à un compte payant, ou utilisez les numéros de test (OTP_NUMEROS_DE_TEST) qui n'envoient aucun message.",
    30454: "Plafond de messages du compte Twilio atteint (compte d'essai) : passez à un compte payant (comptez 3 à 4 h de propagation) ou attendez.",
}


class EnvoiImpossible(Exception):
    """Échec définitif : inutile de réessayer. `explication` est destinée à l'équipe technique."""

    def __init__(self, explication, code=None):
        self.explication = explication
        self.code = code
        super().__init__(explication)

    def __reduce__(self):
        return (self.__class__, (self.explication, self.code))


def explication_erreur_twilio(code):
    return DEFINITIVES.get(code, f"Erreur Twilio {code} : voir https://www.twilio.com/docs/api/errors/{code}")


# ───────────────────────────────────────────────────────────────────────────────────────────────── canaux
def canaux_actifs():
    """Canaux réellement disponibles, dans l'ordre de préférence. Jamais vide : repli sur WhatsApp."""
    valeur = getattr(settings, 'OTP_CANAUX', None)
    if isinstance(valeur, str):
        valeur = [v.strip() for v in valeur.split(',')]
    actifs = [c for c in (valeur or ['whatsapp', 'sms']) if c in CANAUX_CONNUS]
    return actifs or ['whatsapp']


def choisir_canal(demande=None):
    """Canal demandé s'il est disponible, sinon le premier canal actif (ex. SMS demandé alors que seul WhatsApp existe)."""
    actifs = canaux_actifs()
    return demande if demande in actifs else actifs[0]


def _chiffres(numero):
    return ''.join(c for c in str(numero) if c.isdigit())


def numeros_de_test():
    """Numéros pour lesquels aucun message n'est envoyé : le code est FIXE (OTP_CODE_DE_TEST). Vide tant que les deux réglages ne sont pas définis."""
    liste = getattr(settings, 'OTP_NUMEROS_DE_TEST', None) or []
    if isinstance(liste, str):
        liste = [v for v in liste.split(',')]
    if not (getattr(settings, 'OTP_CODE_DE_TEST', '') or '').strip():
        return []
    return [_chiffres(n) for n in liste if _chiffres(n)]


def est_numero_de_test(telephone):
    return _chiffres(telephone) in numeros_de_test()


def generer_code(telephone):
    """Code à 6 chiffres : aléatoire, sauf pour un numéro de test explicitement déclaré (démonstration, relecteurs des stores, essais)."""
    import random
    if est_numero_de_test(telephone):
        return getattr(settings, 'OTP_CODE_DE_TEST').strip()
    return f"{random.randint(0, 999999):06d}"


def aide_bac_a_sable():
    """Données pour l'aide « rejoindre le bac à sable WhatsApp » affichée aux testeurs (None hors mode test)."""
    code = getattr(settings, 'TWILIO_SANDBOX_JOIN_CODE', '') or ''
    expediteur = str(getattr(settings, 'TWILIO_WHATSAPP_FROM', '') or '')
    if not code or 'whatsapp' not in canaux_actifs():
        return None
    return {'numero': expediteur, 'message': code.strip()}


# ───────────────────────────────────────────────────────────────────────────────────────────────── Twilio
def _client_twilio():
    from twilio.rest import Client
    sid, jeton = getattr(settings, 'TWILIO_ACCOUNT_SID', ''), getattr(settings, 'TWILIO_AUTH_TOKEN', '')
    if not sid or not jeton:
        raise EnvoiImpossible("Identifiants Twilio absents : renseignez TWILIO_ACCOUNT_SID et TWILIO_AUTH_TOKEN.")
    return Client(sid, jeton)


def _variables_modele(code):
    brut = getattr(settings, 'TWILIO_WHATSAPP_CONTENT_VARIABLES', '') or '{"1":"{code}"}'
    try:
        modele = json.loads(brut)
    except ValueError:
        raise EnvoiImpossible("TWILIO_WHATSAPP_CONTENT_VARIABLES n'est pas du JSON valide, ex. {\"1\":\"{code}\"}.")
    return {cle: str(valeur).replace('{code}', code).replace('{service}', SERVICE) for cle, valeur in modele.items()}


def envoyer_whatsapp_twilio(telephone, code):
    """Envoie le code par WhatsApp (modèle si TWILIO_WHATSAPP_CONTENT_SID, sinon message libre). Retourne le SID du message Twilio."""
    from twilio.base.exceptions import TwilioRestException
    client = _client_twilio()
    expediteur = f"whatsapp:{getattr(settings, 'TWILIO_WHATSAPP_FROM', '+14155238886')}"
    destinataire = f"whatsapp:{telephone}"

    parametres = {'from_': expediteur, 'to': destinataire}
    contenu_sid = getattr(settings, 'TWILIO_WHATSAPP_CONTENT_SID', '') or ''
    if contenu_sid:
        parametres.update(content_sid=contenu_sid, content_variables=json.dumps(_variables_modele(code)))
    else:
        parametres['body'] = f"Votre code {SERVICE} est : {code}. Il expire dans 5 minutes."

    try:
        message = client.messages.create(**parametres)
    except TwilioRestException as exc:
        if exc.code in DEFINITIVES:
            raise EnvoiImpossible(explication_erreur_twilio(exc.code), exc.code) from exc
        raise      # panne passagère (réseau, 5xx, limite de débit) : la tâche réessaiera
    return message.sid
