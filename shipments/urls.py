from django.urls import path
from rest_framework.routers import DefaultRouter
from .views import BulkChangeStatusView, ColisViewSet, LotDedouanementViewSet, TarifViewSet, IncidentViewSet, BulkAssignLotView

router = DefaultRouter()
router.register('colis', ColisViewSet, basename='colis')
router.register('lots-dedouanement', LotDedouanementViewSet, basename='lots-dedouanement')
router.register('tarifs', TarifViewSet, basename='tarifs')
router.register('incidents', IncidentViewSet, basename='incidents')

urlpatterns = [
    path('colis/bulk-assign-lot/', BulkAssignLotView.as_view()),  
    path('colis/bulk-change-status/', BulkChangeStatusView.as_view()),  
] + router.urls