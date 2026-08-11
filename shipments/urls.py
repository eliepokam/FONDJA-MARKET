from rest_framework.routers import DefaultRouter
from .views import ColisViewSet

router = DefaultRouter()
router.register('colis', ColisViewSet, basename='colis')

urlpatterns = router.urls