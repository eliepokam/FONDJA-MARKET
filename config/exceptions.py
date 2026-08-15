import logging
from rest_framework.views import exception_handler
from rest_framework.response import Response
from rest_framework import status

logger = logging.getLogger('django')

def custom_exception_handler(exc, context):
    response = exception_handler(exc, context)

    if response is not None:
         # DRF a su gérer l'exception (validation, 404, permission...) — on ne touche à rien.
        return response

    # Exception imprévue (bug, erreur DB...) : on logge la vraie trace pour nous,
    # on ne renvoie JAMAIS ça au client.
    logger.error(f"Erreur non gérée sur {context['request'].path}", exc_info=exc)
    return Response(
        {'detail': "Une erreur interne est survenue. Réessayez plus tard."},
        status=status.HTTP_500_INTERNAL_SERVER_ERROR
    )