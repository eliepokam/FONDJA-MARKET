from rest_framework import generics
from .models import Notification
from .serializers import NotificationSerializer
from rest_framework.views import APIView
from rest_framework.response import Response
from .models import FCMToken, Notification
from django.shortcuts import get_object_or_404
from django.utils import timezone


class MesNotificationsView(generics.ListAPIView):
    serializer_class = NotificationSerializer

    def get_queryset(self):
        return Notification.objects.filter(utilisateur=self.request.user).order_by('-cree_le')


class RegisterPushTokenView(APIView):
    def post(self, request):
        token = request.data.get('token')
        if not token:
            return Response({'detail': 'Token manquant.'}, status=400)
        FCMToken.objects.update_or_create(
            token=token,
            defaults={'utilisateur': request.user},
        )
        return Response({'detail': 'Token enregistré.'})

class UnreadNotificationsCountView(APIView):
    def get(self, request):
        count = Notification.objects.filter(utilisateur=request.user, lu_le__isnull=True).count()
        return Response({'count': count})

class MarkAllNotificationsReadView(APIView):
    def post(self, request):
        updated = Notification.objects.filter(
            utilisateur=request.user, lu_le__isnull=True
        ).update(lu_le=timezone.now())
        return Response({'detail': f'{updated} notification(s) marquée(s) comme lue(s).', 'count': updated})


class MarkNotificationReadView(APIView):
    """Marque UNE notification comme lue — uniquement si elle appartient à l'utilisateur connecté."""

    def _marquer(self, request, pk):
        notification = get_object_or_404(Notification, pk=pk, utilisateur=request.user)
        if notification.lu_le is None:
            notification.lu_le = timezone.now()
            notification.save(update_fields=['lu_le'])
        return Response(NotificationSerializer(notification).data)

    def patch(self, request, pk):
        return self._marquer(request, pk)

    def post(self, request, pk):
        return self._marquer(request, pk)
