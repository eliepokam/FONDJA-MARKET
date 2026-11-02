from django.urls import path
from .views import (
    MarkAllNotificationsReadView, MarkNotificationReadView, MesNotificationsView,
    RegisterPushTokenView, UnreadNotificationsCountView,
)

urlpatterns = [
    path('notifications/', MesNotificationsView.as_view()),
    path('notifications/unread-count/', UnreadNotificationsCountView.as_view()),
    path('notifications/push-token/', RegisterPushTokenView.as_view()),
    path('notifications/mark-all-read/', MarkAllNotificationsReadView.as_view()),
    path('notifications/<int:pk>/', MarkNotificationReadView.as_view()),
    # Anciennes routes, conservées pour ne rien casser chez les clients déjà déployés.
    path('push-token/', RegisterPushTokenView.as_view()),
    path('unread-count/', UnreadNotificationsCountView.as_view()),
]
