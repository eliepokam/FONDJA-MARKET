from django.shortcuts import get_object_or_404
from django.utils import timezone
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework import generics, permissions, status
from rest_framework.parsers import MultiPartParser, FormParser

from shipments.models import Colis
from shipments.status import TransitionInvalide, changer_statut
from notifications.models import Notification
from users.permissions import IsAgent, IsAdministrateur
from .models import HistoriqueStatutColis
from .models import PhotoColis
from .serializers import PhotoColisSerializer
from .serializers import ChangerStatutSerializer
from rest_framework.exceptions import PermissionDenied
from shipments.models import Tarif
from datetime import timedelta

class ChangerStatutColisView(APIView):
    permission_classes = [(IsAgent | IsAdministrateur)]

    def post(self, request, code_suivi):
        colis = get_object_or_404(Colis, code_suivi=code_suivi)
        serializer = ChangerStatutSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        nouveau_statut = serializer.validated_data['statut']
        try:
            changer_statut(colis, nouveau_statut, request.user, serializer.validated_data.get('note', ''))
        except TransitionInvalide as exc:
            return Response({'detail': str(exc)}, status=status.HTTP_400_BAD_REQUEST)

        return Response({'detail': 'Statut mis à jour.', 'statut': nouveau_statut})

class PhotoColisCreateView(generics.ListCreateAPIView):
    serializer_class = PhotoColisSerializer
    parser_classes = [MultiPartParser, FormParser]

    def get_queryset(self):
        colis = get_object_or_404(Colis, code_suivi=self.kwargs['code_suivi'])
        user = self.request.user
        is_staff = hasattr(user, 'agent') or hasattr(user, 'administrateur')
        is_owner = hasattr(user, 'client') and colis.client_id == user.client.id
        if not (is_staff or is_owner):
            return PhotoColis.objects.none()
        return PhotoColis.objects.filter(colis=colis)

    def get_permissions(self):
        return [permissions.IsAuthenticated()]

    def perform_create(self, serializer):
        user = self.request.user
        colis = get_object_or_404(Colis, code_suivi=self.kwargs['code_suivi'])

        is_staff = hasattr(user, 'agent') or hasattr(user, 'administrateur')
        is_owner = hasattr(user, 'client') and colis.client_id == user.client.id
        if not (is_staff or is_owner):
            raise PermissionDenied("Vous n'avez pas le droit d'ajouter une photo à ce colis.")

        serializer.save(
            colis=colis,
            agent=user.agent if hasattr(user, 'agent') else None,
            prise_le=timezone.now(),
        )