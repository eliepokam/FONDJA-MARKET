from django.shortcuts import get_object_or_404
from django.utils import timezone
from rest_framework.views import APIView
from rest_framework.response import Response

from shipments.models import Colis
from notifications.models import Notification
from users.permissions import IsAgent
from .models import HistoriqueStatutColis
from .serializers import ChangerStatutSerializer


class ChangerStatutColisView(APIView):
    permission_classes = [IsAgent]

    def post(self, request, code_suivi):
        colis = get_object_or_404(Colis, code_suivi=code_suivi)
        serializer = ChangerStatutSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        nouveau_statut = serializer.validated_data['statut']
        note = serializer.validated_data.get('note', '')

        HistoriqueStatutColis.objects.create(
            colis=colis, statut=nouveau_statut, agent=request.user.agent, note=note
        )
        colis.statut = nouveau_statut
        colis.save()

        Notification.objects.create(
            utilisateur=colis.client,
            colis=colis,
            type=Notification.Type.STATUT_COLIS,
            canal=Notification.Canal.PUSH,
            contenu=f"Votre colis {colis.code_suivi} est maintenant : {colis.get_statut_display()}",
            envoye_le=timezone.now(),
        )

        return Response({'detail': 'Statut mis à jour.', 'statut': nouveau_statut})