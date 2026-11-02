"""
Client HTTP pour CamPay (agrégateur camerounais : MTN Mobile Money + Orange Money).

Repris du SDK officiel (pip install campay) : mêmes URLs, même authentification, mêmes champs —
SAUF que le SDK désactive la vérification TLS (verify=False) et BLOQUE jusqu'à la fin de la transaction ;
ici la vérification TLS reste active et l'appel rend la main tout de suite (statut sondé ensuite).

Expose la MÊME interface que FreeMoPayClient (create_payment / get_status) et normalise les statuts
(SUCCESSFUL -> SUCCESS), donc les vues, le front et les tests n'ont pas à connaître le prestataire.

Environnements : demo -> https://demo.campay.net   |   production -> https://www.campay.net
Documentation : https://documenter.getpostman.com/view/2391374/T1LV8PVA
"""
import logging

import requests
from django.conf import settings

from .services import FreeMoPayClient, FreeMoPayError

logger = logging.getLogger('django')

# Codes d'erreur documentés par CamPay -> message clair pour le client.
MESSAGES_PAR_CODE = {
    'ER101': "Numéro de téléphone invalide : utilise le format 237XXXXXXXXX.",
    'ER102': "Seuls les numéros MTN et Orange sont acceptés pour le Mobile Money.",
    'ER201': "Montant invalide.",
}

# Statut CamPay -> vocabulaire commun (celui de FREEMOPAY_STATUS_MAP)
STATUTS_NORMALISES = {
    'PENDING': 'PENDING',
    'SUCCESSFUL': 'SUCCESS',
    'FAILED': 'FAILED',
}


class CamPayClient:
    def __init__(self):
        self.base_url = settings.CAMPAY_BASE_URL.rstrip('/')
        self.username = settings.CAMPAY_APP_USERNAME
        self.password = settings.CAMPAY_APP_PASSWORD

    # ------------------------------------------------------------------ authentification
    def _headers(self):
        try:
            response = requests.post(
                f'{self.base_url}/api/token/',
                json={'username': self.username, 'password': self.password},
                timeout=15,
            )
        except requests.RequestException as exc:
            logger.error("CamPay injoignable lors de l'authentification", exc_info=exc)
            raise FreeMoPayError("Impossible de contacter le service de paiement pour le moment.") from exc

        token = None
        if response.status_code == 200:
            try:
                token = response.json().get('token')
            except ValueError:
                token = None
        if not token:
            logger.error(f"CamPay : authentification refusée ({response.status_code}). "
                         f"Vérifie CAMPAY_APP_USERNAME / CAMPAY_APP_PASSWORD et CAMPAY_BASE_URL (démo ou production).")
            raise FreeMoPayError("Le service de paiement est mal configuré.", status_code=response.status_code)
        return {'Authorization': f'Token {token}', 'Content-Type': 'application/json'}

    @staticmethod
    def _message_lisible(data):
        if not isinstance(data, dict):
            return None
        brut = str(data)
        for code, message in MESSAGES_PAR_CODE.items():
            if code in brut:
                return message
        message = data.get('message')
        if isinstance(message, dict):
            message = message.get('fr') or message.get('en')
        if isinstance(message, str) and message.strip():
            return message.strip()
        return None

    # ------------------------------------------------------------------ collecte
    def create_payment(self, *, amount, payer_phone, external_id, description, callback_url=None):
        """
        Demande un paiement (POST /api/collect/) : CamPay envoie la demande de validation sur le
        téléphone du payeur et répond tout de suite avec la `reference` (statut initial PENDING).
        `callback_url` est ignoré : le webhook CamPay se configure dans le tableau de bord de l'application.
        """
        payload = {
            'amount': FreeMoPayClient._format_amount(amount),   # entier sans décimales (code ER201 sinon)
            'currency': 'XAF',
            'from': payer_phone,                                # indicatif inclus, sans "+" : 2376XXXXXXXX
            'description': str(description)[:255],
            'external_reference': str(external_id),
        }
        headers = self._headers()
        try:
            response = requests.post(f'{self.base_url}/api/collect/', json=payload, headers=headers, timeout=20)
        except requests.RequestException as exc:
            logger.error("CamPay injoignable lors de la création du paiement", exc_info=exc)
            raise FreeMoPayError("Impossible de contacter le service de paiement pour le moment.") from exc

        try:
            data = response.json() if response.content else {}
        except ValueError as exc:
            logger.error(f"Réponse CamPay illisible ({response.status_code}) : {response.text[:500]}")
            raise FreeMoPayError("Réponse inattendue du service de paiement.") from exc
        if not isinstance(data, dict):
            data = {}

        if response.status_code != 200 or not data.get('reference'):
            logger.error(f"CamPay a refusé la création du paiement ({response.status_code}) : {data}")
            raise FreeMoPayError(
                self._message_lisible(data) or "Le service de paiement a refusé la demande.",
                status_code=response.status_code,
            )
        return {'reference': data['reference'], 'status': 'PENDING', 'ussd_code': data.get('ussd_code'),
                'operator': data.get('operator')}

    # ------------------------------------------------------------------ statut
    def get_status(self, reference):
        """Vrai statut d'une transaction (GET /api/transaction/<reference>/), normalisé en PENDING/SUCCESS/FAILED."""
        headers = self._headers()
        try:
            response = requests.get(f'{self.base_url}/api/transaction/{reference}/', headers=headers, timeout=15)
        except requests.RequestException as exc:
            logger.error("CamPay injoignable lors de la vérification de statut", exc_info=exc)
            raise FreeMoPayError("Impossible de contacter le service de paiement pour le moment.") from exc

        if response.status_code != 200:
            raise FreeMoPayError(
                f"Le service de paiement a répondu {response.status_code} lors de la vérification de statut.",
                status_code=response.status_code,
            )
        try:
            data = response.json()
        except ValueError as exc:
            raise FreeMoPayError("Réponse inattendue du service de paiement.") from exc

        brut = data.get('status') if isinstance(data, dict) else None
        return {**data, 'status': STATUTS_NORMALISES.get(brut, brut)}
