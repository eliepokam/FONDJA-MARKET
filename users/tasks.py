import logging
import requests
from celery import shared_task
from django.conf import settings
from twilio.rest import Client

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


def _envoyer_whatsapp_twilio(telephone, message):
    client = Client(settings.TWILIO_ACCOUNT_SID, settings.TWILIO_AUTH_TOKEN)
    client.messages.create(
        body=message,
        from_=f"whatsapp:{settings.TWILIO_WHATSAPP_FROM}",
        to=f"whatsapp:{telephone}",
    )


@shared_task(bind=True, max_retries=3, default_retry_delay=10)
def envoyer_otp_task(self, telephone, code, canal):
    message = f"Votre code Fondja Market est : {code}. Il expire dans 5 minutes."
    if getattr(settings, 'DISABLE_EXTERNAL_MESSAGING', False):
        logger.info(f"[ENVOI DÉSACTIVÉ] OTP pour {telephone} via {canal}")
        return
    try:
        if canal == 'whatsapp':
            _envoyer_whatsapp_twilio(telephone, message)
        else:
            _envoyer_sms_devcode(telephone, message)
        logger.info(f"OTP envoyé à {telephone} via {canal}")

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