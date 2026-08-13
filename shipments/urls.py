from rest_framework.routers import DefaultRouter
from .views import ColisViewSet, LotDedouanementViewSet, TarifViewSet

router = DefaultRouter()
router.register('colis', ColisViewSet, basename='colis')
router.register('lots-dedouanement', LotDedouanementViewSet, basename='lots-dedouanement')
router.register('tarifs', TarifViewSet, basename='tarifs')

urlpatterns = router.urls