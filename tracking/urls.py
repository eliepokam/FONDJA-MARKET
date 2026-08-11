from django.urls import path
from .views import ChangerStatutColisView

urlpatterns = [
    path('colis/<str:code_suivi>/statut/', ChangerStatutColisView.as_view()),
]