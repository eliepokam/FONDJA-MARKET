from django.urls import path
from rest_framework.routers import DefaultRouter
from .views import ReclamationViewSet, MessageReclamationCreateView

router = DefaultRouter()
router.register('reclamations', ReclamationViewSet, basename='reclamation')

urlpatterns = router.urls + [
    path('reclamations/<int:reclamation_id>/messages/', MessageReclamationCreateView.as_view()),
]