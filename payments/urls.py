from django.urls import path
from rest_framework.routers import DefaultRouter
from .views import PaiementViewSet, FreeMoPayWebhookView

router = DefaultRouter()
router.register('paiements', PaiementViewSet, basename='paiement')

urlpatterns = [
    # Appelée par les serveurs de FreeMoPay, pas par le front — voir FreeMoPayWebhookView.
    path('webhooks/freemopay/', FreeMoPayWebhookView.as_view(), name='freemopay-webhook'),
] + router.urls