"""
Pages d'erreur de Django (hors DRF) : JSON français pour l'API, page française sobre pour le reste.
Branchées par `handler400 / 403 / 404 / 500` dans config/urls.py et par CSRF_FAILURE_VIEW.
"""
from django.http import HttpResponse, JsonResponse

MESSAGES = {
    400: "Requête invalide.",
    403: "Accès refusé.",
    404: "Page ou élément introuvable.",
    500: "Une erreur interne est survenue. Réessayez plus tard.",
}


def _reponse(request, statut, message=None):
    message = message or MESSAGES[statut]
    if request.path.startswith('/api/'):
        return JsonResponse({'detail': message}, status=statut)
    html = (
        "<!doctype html><html lang='fr'><head><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'>"
        f"<title>{statut} — FONDJA MARKET</title></head>"
        "<body style=\"font-family:system-ui,sans-serif;max-width:32rem;margin:15vh auto;padding:0 1.5rem;color:#25282B\">"
        f"<p style=\"color:#D90016;font-weight:700;font-size:3rem;margin:0\">{statut}</p><h1 style=\"font-size:1.25rem\">{message}</h1>"
        "<p><a href=\"/\" style=\"color:#D90016\">Retour à l'accueil</a></p></body></html>"
    )
    return HttpResponse(html, status=statut)


def requete_invalide(request, exception=None):
    return _reponse(request, 400)


def acces_refuse(request, exception=None):
    return _reponse(request, 403)


def page_introuvable(request, exception=None):
    return _reponse(request, 404)


def erreur_serveur(request):
    return _reponse(request, 500)


def echec_csrf(request, reason=""):
    """Échec de la vérification CSRF (formulaires de l'interface Django) : message clair au lieu de la page anglaise de Django."""
    return _reponse(request, 403, "Votre session a expiré ou la requête est invalide. Rechargez la page et réessayez.")
