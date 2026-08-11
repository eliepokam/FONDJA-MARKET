from rest_framework import generics
from .models import Notification
from .serializers import NotificationSerializer


class MesNotificationsView(generics.ListAPIView):
    serializer_class = NotificationSerializer

    def get_queryset(self):
        return Notification.objects.filter(utilisateur=self.request.user).order_by('-cree_le')