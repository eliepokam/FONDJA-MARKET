from django.urls import path
from rest_framework.routers import DefaultRouter
from .views import BulkChangeStatusView, ColisViewSet, LotDedouanementViewSet, TarifViewSet, IncidentViewSet, BulkAssignLotView, PublicTarifListView, CoordonneesView, AdminAdressesTransitListView, AdminAdresseTransitDetailView

router = DefaultRouter()
router.register('colis', ColisViewSet, basename='colis')
router.register('lots-dedouanement', LotDedouanementViewSet, basename='lots-dedouanement')
router.register('tarifs', TarifViewSet, basename='tarifs')
router.register('incidents', IncidentViewSet, basename='incidents')

urlpatterns = [
    path('coordonnees/', CoordonneesView.as_view()),
    path('admin/adresses-transit/', AdminAdressesTransitListView.as_view()),
    path('admin/adresses-transit/<str:mode>/', AdminAdresseTransitDetailView.as_view()),
    path('public/tarifs/', PublicTarifListView.as_view()),     # sans authentification : landing page
    path('colis/bulk-assign-lot/', BulkAssignLotView.as_view()),  
    path('colis/bulk-change-status/', BulkChangeStatusView.as_view()),  
] + router.urls