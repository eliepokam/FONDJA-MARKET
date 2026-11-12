from django.shortcuts import get_object_or_404
from django.utils import timezone
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework import generics, permissions, status
from rest_framework.parsers import MultiPartParser, FormParser

from shipments.models import Colis
from shipments.status import TransitionInvalide, changer_statut
from notifications.models import Notification
from users.permissions import IsAgent, IsAdministrateur, IsClient
from .models import HistoriqueStatutColis
from .models import PhotoColis
from .serializers import MediaClientSerializer, PhotoColisSerializer
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


class PhotoColisDetailView(generics.RetrieveDestroyAPIView):
    """
    Voir / supprimer UNE photo d'un colis.
    Le personnel peut tout supprimer. Un client ne peut supprimer que SES propres photos (pas celles de l'entrepôt),
    et seulement tant que le colis n'a pas été pris en charge (statut « déclaré »).
    """
    serializer_class = PhotoColisSerializer

    def get_queryset(self):
        colis = get_object_or_404(Colis, code_suivi=self.kwargs['code_suivi'])
        user = self.request.user
        if hasattr(user, 'agent') or hasattr(user, 'administrateur') or (hasattr(user, 'client') and colis.client_id == user.client.id):
            return PhotoColis.objects.filter(colis=colis)
        return PhotoColis.objects.none()

    def perform_destroy(self, instance):
        user = self.request.user
        if hasattr(user, 'client'):
            if instance.agent_id:
                raise PermissionDenied("Cette photo a été prise par l'équipe : vous ne pouvez pas la supprimer.")
            if instance.colis.statut != Colis.Statut.DECLAREE:
                raise PermissionDenied("Ce colis a déjà été pris en charge : ses photos ne peuvent plus être supprimées.")
        elif hasattr(user, 'agent') and not hasattr(user, 'administrateur') and instance.agent_id != user.agent.id:
            # Photos d'un collègue ou du client : preuves du colis, seul un administrateur peut les retirer.
            raise PermissionDenied("Vous ne pouvez supprimer que les photos que vous avez prises vous-même.")
        if instance.url:
            instance.url.delete(save=False)      # supprime aussi le fichier du disque
        instance.delete()


class MesMediasView(generics.ListAPIView):
    """Espace « Photos et vidéos » du client : tout ce que l'ÉQUIPE a pris de ses colis (réception, pesée, étapes), du plus récent au plus ancien."""
    serializer_class = MediaClientSerializer
    permission_classes = [IsClient]

    def get_queryset(self):
        qs = (PhotoColis.objects.filter(colis__client=self.request.user.client, agent__isnull=False)
              .select_related('colis').order_by('-cree_le', '-id'))
        code = self.request.query_params.get('colis')
        return qs.filter(colis__code_suivi=code) if code else qs
