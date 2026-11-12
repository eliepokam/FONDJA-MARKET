from django.urls import path
from rest_framework.routers import DefaultRouter
from .views import (AdminCompteBancaireViewSet, ChiffreAffairesView, ComptesBancairesView, FreeMoPayWebhookView, PaiementViewSet,
                    PreuveFichierView, VirementsAVerifierView)

router = DefaultRouter()
router.register('paiements', PaiementViewSet, basename='paiement')
router.register('admin/comptes-bancaires', AdminCompteBancaireViewSet, basename='admin-compte-bancaire')

urlpatterns = [
    # Appelée par les serveurs de FreeMoPay, pas par le front — voir FreeMoPayWebhookView.
    path('webhooks/freemopay/', FreeMoPayWebhookView.as_view(), name='freemopay-webhook'),
    path('paiements/<int:pk>/preuve-fichier/', PreuveFichierView.as_view()),
    path('virements/', VirementsAVerifierView.as_view()),
    path('comptes-bancaires/', ComptesBancairesView.as_view()),
    path('admin/chiffre-affaires/', ChiffreAffairesView.as_view()),
] + router.urls