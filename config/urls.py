"""
URL configuration for config project.

The `urlpatterns` list routes URLs to views. For more information please see:
    https://docs.djangoproject.com/en/6.0/topics/http/urls/
Examples:
Function views
    1. Add an import:  from my_app import views
    2. Add a URL to urlpatterns:  path('', views.home, name='home')
Class-based views
    1. Add an import:  from other_app.views import Home
    2. Add a URL to urlpatterns:  path('', Home.as_view(), name='home')
Including another URLconf
    1. Import the include() function: from django.urls import include, path
    2. Add a URL to urlpatterns:  path('blog/', include('blog.urls'))
"""

from django.contrib import admin
from django.urls import path, include, re_path
from django.conf import settings
from django.views.static import serve
from drf_spectacular.views import SpectacularAPIView, SpectacularSwaggerView
from users.views import CreateStaffAccountView, UserListView, ToggleUserStatusView, DeleteUserView
from core.views import AdminStatsView, ExportDataView
from core.views import JournalActiviteListView
import logging

try:   # lot « API WhatsApp Meta » : FACULTATIF. Sans lui, la route du webhook n'existe simplement pas (le reste du site fonctionne).
    from users.webhooks import WhatsappWebhookView
except ImportError as exc:
    WhatsappWebhookView = None
    logging.getLogger('django').info(f"Webhook WhatsApp (Meta) non installé : route ignorée ({exc})")


def servir_medias():
    """
    Photos et vidéos envoyées par les utilisateurs (/media/…).

    `static()` ne fait RIEN quand DEBUG est faux : les images disparaîtraient. Pour un hébergement de test exposé sur Internet on veut
    DEBUG=False (pas de pages d'erreur détaillées visibles de tous) ET les médias servis par Django : SERVE_MEDIA=True.
    En production réelle, laisser Nginx servir /media/ directement (SERVE_MEDIA absent ou faux).
    """
    if settings.DEBUG or getattr(settings, 'SERVE_MEDIA', False):
        prefixe = settings.MEDIA_URL.lstrip('/')
        return [re_path(rf'^{prefixe}(?P<path>.*)$', serve, {'document_root': settings.MEDIA_ROOT})]
    return []


urlpatterns = [
    path('django-admin/', admin.site.urls),   # « /admin » est réservé à l'espace administrateur de l'application (front)
    path('api/auth/', include('users.urls')),
    path('api/', include('shipments.urls')),
    path('api/schema/', SpectacularAPIView.as_view(), name='schema'),
    path('api/docs/', SpectacularSwaggerView.as_view(url_name='schema')),
    path('api/', include('tracking.urls')),
    path('api/', include('notifications.urls')),
    path('api/', include('payments.urls')),
    path('api/', include('complaints.urls')),
    path('api/users/', UserListView.as_view()),
    path('api/admin/users/<int:user_id>/statut/', ToggleUserStatusView.as_view()),
    path('api/admin/users/<int:user_id>/', DeleteUserView.as_view()),
    path('api/admin/staff-accounts/', CreateStaffAccountView.as_view()),
    path('api/admin/stats/', AdminStatsView.as_view()),
    path('api/admin/export/', ExportDataView.as_view()),
    path('api/admin/journal/', JournalActiviteListView.as_view()),
] + servir_medias()

if WhatsappWebhookView is not None:
    urlpatterns.insert(0, path('api/webhooks/whatsapp/', WhatsappWebhookView.as_view()))   # notifications de Meta (statuts de livraison, alertes)

# Pages d'erreur : JSON français pour /api/, page française sinon
handler400 = 'config.handlers.requete_invalide'
handler403 = 'config.handlers.acces_refuse'
handler404 = 'config.handlers.page_introuvable'
handler500 = 'config.handlers.erreur_serveur'