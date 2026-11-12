import logging
import requests
from celery import shared_task
from django.conf import settings
from .messaging import EnvoiImpossible, envoyer_whatsapp_twilio, est_numero_de_test

logger = logging.getLogger('django')


class DevCodeSMSError(Exception):
    def __init__(self, code, message):
        self.code = code
        self.message = message
        super().__init__(code, message)

    def __reduce__(self):
        # Dit explicitement à pickle comment reconstruire l'objet :
        # rappeler DevCodeSMSError(code, message) — sans ça, Celery ne sait
        # pas recréer l'exception une fois transmise entre processus.
        return (self.__class__, (self.code, self.message))


def _envoyer_sms_devcode(telephone, message):
    payload = {
        'api_key': settings.DEVCODE_SMS_API_KEY,
        'sender': settings.DEVCODE_SMS_SENDER_ID,
        'phone': str(telephone),
        'message': message,
    }
    response = requests.post(
        'https://devcodesms.com/developpeur/Send_sms_dev',
        json=payload,
        timeout=10,
    )
    logger.debug(f"Réponse brute DevCode : {response.status_code} {response.text}")
    data = response.json()
    if data.get('status') != 200:
        raise DevCodeSMSError(data.get('code'), data.get('message'))
    return data


def _envoyer_whatsapp_twilio(telephone, code):
    return envoyer_whatsapp_twilio(telephone, code)


@shared_task(bind=True, max_retries=3, default_retry_delay=10)
def envoyer_otp_task(self, telephone, code, canal):
    message = f"Votre code Fondja Market est : {code}. Il expire dans 5 minutes."
    if est_numero_de_test(telephone):
        logger.info(f"[NUMÉRO DE TEST] aucun message envoyé à {telephone} : utilisez le code de test (OTP_CODE_DE_TEST)")
        return
    if getattr(settings, 'DISABLE_EXTERNAL_MESSAGING', False):
        logger.info(f"[ENVOI DÉSACTIVÉ] OTP pour {telephone} via {canal}")
        return
    try:
        if canal == 'whatsapp':
            sid = _envoyer_whatsapp_twilio(telephone, code)
            logger.info(f"OTP envoyé à {telephone} via whatsapp (message Twilio {sid})")
            return
        _envoyer_sms_devcode(telephone, message)
        logger.info(f"OTP envoyé à {telephone} via {canal}")

    except EnvoiImpossible as exc:
        # Échec définitif (destinataire pas dans le bac à sable, identifiants refusés...) : on arrête, avec une explication lisible.
        logger.error(f"Échec DÉFINITIF OTP {telephone} via {canal} : {exc.explication}")
        return

    except DevCodeSMSError as exc:
        # Certaines erreurs sont définitives : retenter n'y changera rien
        # (mauvaise clé, plus de crédit, numéro invalide) — inutile de gaspiller les 3 essais.
        if exc.code in ('unauthorized', 'insufficient_balance', 'bad_request'):
            logger.error(f"Échec DÉFINITIF OTP {telephone} : {exc}")
            raise
        logger.warning(f"Échec temporaire OTP {telephone} : {exc}, nouvelle tentative...")
        raise self.retry(exc=exc)

    except Exception as exc:
        logger.error(f"Échec envoi OTP à {telephone} via {canal} : {exc}")
        raise self.retry(exc=exc)