from django.urls import path
from .views import ChangerStatutColisView, PhotoColisCreateView

urlpatterns = [
    path('colis/<str:code_suivi>/statut/', ChangerStatutColisView.as_view()),
    path('colis/<str:code_suivi>/photos/', PhotoColisCreateView.as_view()),
]