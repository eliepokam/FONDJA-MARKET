from django.utils import translation


class FrancaisMiddleware:
    """Force le français pour chaque requête, même si LANGUAGE_CODE est mal réglé : les messages de DRF, Django et des formulaires restent en français."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        with translation.override('fr'):
            return self.get_response(request)
