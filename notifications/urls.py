from django.urls import path
from .views import MesNotificationsView

urlpatterns = [
    path('notifications/', MesNotificationsView.as_view()),
]