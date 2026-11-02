from django.urls import path
from rest_framework.routers import DefaultRouter
from .views import ReclamationViewSet, MessageReclamationCreateView, PhotoReclamationCreateView

router = DefaultRouter()
router.register('reclamations', ReclamationViewSet, basename='reclamation')

urlpatterns = router.urls + [
    path('reclamations/<int:reclamation_id>/messages/', MessageReclamationCreateView.as_view()),
    path('reclamations/<int:reclamation_id>/photos/', PhotoReclamationCreateView.as_view()),
]