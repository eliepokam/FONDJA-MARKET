"""
Client HTTP pour l'agrégateur de paiement FreeMoPay.

Documentation officielle (Postman) :
https://documenter.getpostman.com/view/18350660/2sAY55cJUy

Flux résumé :
1. initier() (vue) appelle FreeMoPayClient.create_payment() -> FreeMoPay
   envoie un push USSD directement sur le téléphone du payeur (pas de page
   de paiement à afficher, contrairement à CamerPay — donc pas de
   checkout_url ni de return_url).
2. Le client valide (ou annule) sur son téléphone.
3. FreeMoPay notifie NOTRE serveur en tâche de fond via un webhook HTTP POST
   sur FREEMOPAY_CALLBACK_URL (voir views.FreeMoPayWebhookView).

⚠️ IMPORTANT — pas de signature sur le webhook :
Contrairement à CamerPay (HMAC-SHA256), FreeMoPay n'authentifie PAS ses
callbacks. N'importe qui devinant l'URL du webhook pourrait forger un faux
"status": "SUCCESS". La parade : ne JAMAIS faire confiance au contenu du
webhook reçu — l'utiliser uniquement comme signal pour aller chercher le
VRAI statut via get_status() (authentifié par nos identifiants secrets),
avant de mettre à jour quoi que ce soit en base.
"""
import logging

import requests
from django.conf import settings

logger = logging.getLogger('django')


class FreeMoPayError(Exception):
    """
    Levée quand FreeMoPay renvoie une erreur ou une réponse inattendue.
    `str(exc)` est un message LISIBLE par le client final ; `code` / `status_code` gardent
    le détail technique du prestataire pour les logs et les tests.
    """

    def __init__(self, message, code=None, status_code=None):
        super().__init__(message)
        self.code = code
        self.status_code = status_code


# Codes d'erreur FreeMoPay connus -> message clair pour le client.
MESSAGES_PAR_CODE = {
    'CASHIN_DISABLED': "Le paiement Mobile Money est momentanément indisponible (maintenance du prestataire). "
                       "Réessaie dans quelques minutes.",
}


def _message_lisible(data):
    """
    FreeMoPay renvoie `message` tantôt en texte, tantôt en dictionnaire de langues
    ({'fr': ..., 'en': ...}) : on en tire toujours une phrase, jamais un dict affiché tel quel.
    """
    if not isinstance(data, dict):
        return None
    if data.get('code') in MESSAGES_PAR_CODE:
        return MESSAGES_PAR_CODE[data['code']]
    message = data.get('message')
    if isinstance(message, dict):
        message = message.get('fr') or message.get('en')
    if isinstance(message, str) and message.strip():
        return message.strip()
    return None


class FreeMoPayClient:
    def __init__(self):
        self.base_url = settings.FREEMOPAY_BASE_URL.rstrip('/')
        self.app_key = settings.FREEMOPAY_APP_KEY
        self.secret_key = settings.FREEMOPAY_SECRET_KEY

    def _auth(self):
        # Basic Auth (username=appKey, password=secretKey) — l'une des deux
        # méthodes documentées, la plus simple : pas de gestion d'expiration
        # de token à gérer contrairement au JWT de /payment/token.
        return requests.auth.HTTPBasicAuth(self.app_key, self.secret_key)

    @staticmethod
    def _format_amount(amount):
        # Le XAF n'a pas de centimes : on envoie "35000" et non "35000.00".
        if amount == amount.to_integral_value():
            return str(int(amount))
        return str(amount)

    def create_payment(self, *, amount, payer_phone, external_id, description, callback_url):
        """
        Initie une transaction (POST /api/v2/payment). Renvoie le JSON de
        réponse ('reference', 'status', 'message'). Ce 'status' initial ne
        veut PAS dire que le paiement a réussi — juste que la demande a été
        acceptée ; le vrai résultat arrive plus tard (webhook ou get_status()).
        Lève FreeMoPayError si FreeMoPay refuse ou si le service est injoignable.
        """
        payload = {
            'payer': payer_phone,
            # La doc montre "amount" comme une CHAÎNE ("100"), pas un nombre —
            # confirmé par l'exemple de requête réel.
            'amount': self._format_amount(amount),
            'externalId': external_id,
            'description': description,
            'callback': callback_url,
        }
        try:
            response = requests.post(
                f'{self.base_url}/api/v2/payment',
                json=payload,
                auth=self._auth(),
                timeout=15,
            )
        except requests.RequestException as exc:
            logger.error("FreeMoPay injoignable lors de la création du paiement", exc_info=exc)
            raise FreeMoPayError("Impossible de contacter FreeMoPay pour le moment.") from exc

        try:
            data = response.json() if response.content else {}
        except ValueError as exc:
            logger.error(f"Réponse FreeMoPay illisible ({response.status_code}) : {response.text[:500]}")
            raise FreeMoPayError("Réponse inattendue de FreeMoPay.") from exc

        if response.status_code >= 400:
            logger.error(f"FreeMoPay a refusé la création du paiement ({response.status_code}) : {data}")
            raise FreeMoPayError(
                _message_lisible(data) or "FreeMoPay a refusé la demande de paiement.",
                code=data.get('code') if isinstance(data, dict) else None,
                status_code=response.status_code,
            )

        return data

    def get_status(self, reference):
        """
        Interroge FreeMoPay pour connaître le VRAI statut d'une transaction
        (GET /api/v2/payment/{reference}). C'est cet appel, authentifié par
        nos identifiants secrets, qui fait foi — jamais le contenu brut d'un
        webhook reçu (voir avertissement en tête de fichier).
        """
        try:
            response = requests.get(
                f'{self.base_url}/api/v2/payment/{reference}',
                auth=self._auth(),
                timeout=15,
            )
        except requests.RequestException as exc:
            logger.error("FreeMoPay injoignable lors de la vérification de statut", exc_info=exc)
            raise FreeMoPayError("Impossible de contacter FreeMoPay pour le moment.") from exc

        if response.status_code != 200:
            raise FreeMoPayError(f"FreeMoPay a répondu {response.status_code} lors de la vérification de statut.")

        return response.json()


# Mapping statut FreeMoPay -> statut interne Paiement (payments.models.Paiement.Statut)
FREEMOPAY_STATUS_MAP = {
    'PENDING': 'en_attente',
    'SUCCESS': 'paye',
    'FAILED': 'echoue',
}


def get_gateway():
    """
    Renvoie le client du prestataire de paiement actif (variable d'environnement PAYMENT_PROVIDER :
    "freemopay" par défaut, ou "campay"). Les deux exposent la même interface.
    """
    if getattr(settings, 'PAYMENT_PROVIDER', 'freemopay') == 'campay':
        from .services_campay import CamPayClient   # import local : évite un import circulaire
        return CamPayClient()
    return FreeMoPayClient()
