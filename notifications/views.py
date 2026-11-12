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


class TestPushView(APIView):
    """
    « Tester mes notifications » : envoie TOUT DE SUITE une notification push à tous les appareils de l'utilisateur connecté et dit pourquoi
    elle n'arrive pas, le cas échéant (aucun appareil enregistré, Firebase non configuré, jeton d'un autre projet…).
    """
    from rest_framework.throttling import UserRateThrottle as _Throttle

    class LimiteTest(_Throttle):
        scope = 'test_push'
        rate = '20/hour'

    throttle_classes = [LimiteTest]

    def post(self, request):
        from .services import firebase_pret, send_push_notification
        if not firebase_pret():
            return Response({'detail': "Les notifications push ne sont pas configurées sur le serveur "
                                       "(fichier d'identifiants Firebase absent ou invalide : voir FIREBASE_CREDENTIALS_PATH)."}, status=503)
        resume = send_push_notification(request.user, "Test de notification", "Si vous lisez ce message, les notifications fonctionnent.", "/notifications")
        if resume['tokens'] == 0:
            resume['detail'] = ("Aucun appareil n'est enregistré pour votre compte. Activez les notifications depuis ce navigateur "
                                "(autorisation puis bouton « Activer »), puis réessayez.")
        elif resume['envoyes'] == 0:
            resume['detail'] = "Firebase a refusé l'envoi : " + "; ".join(e['raison'] for e in resume['erreurs'])
        else:
            resume['detail'] = f"Notification envoyée à {resume['envoyes']} appareil(s). Si rien n'apparaît, vérifiez l'autorisation du navigateur."
        return Response(resume)
