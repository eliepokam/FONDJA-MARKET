from django.urls import path
from .views import ChangerStatutColisView, MesMediasView, PhotoColisCreateView, PhotoColisDetailView

urlpatterns = [
    path('colis/<str:code_suivi>/statut/', ChangerStatutColisView.as_view()),
    path('colis/<str:code_suivi>/photos/', PhotoColisCreateView.as_view()),
    path('colis/<str:code_suivi>/photos/<int:pk>/', PhotoColisDetailView.as_view()),
    path('medias/', MesMediasView.as_view()),
]