from django.utils import timezone
from rest_framework import viewsets, permissions
from rest_framework.decorators import action
from rest_framework.response import Response

from .models import Paiement
from .serializers import PaiementSerializer
from users.permissions import IsClient, IsAdministrateur
# Create your views here.

class PaiementViewSet(viewsets.ModelViewSet):
    serializer_class = PaiementSerializer

    def get_queryset(self):
        user = self.request.user
        if hasattr(user, 'client'):
            return Paiement.objects.filter(client=user.client)
        return Paiement.objects.all()  # administrateur/agent voient tout

    def perform_create(self, serializer):
        serializer.save(client=self.request.user.client)

    def get_permissions(self):
        if self.action == 'create':
            return [IsClient()]
        if self.action == 'confirmer':
            return [IsAdministrateur()]
        return [permissions.IsAuthenticated()]

    @action(detail=True, methods=['post'])
    def confirmer(self, request, pk=None):
        paiement = self.get_object()
        paiement.statut = Paiement.Statut.PAYE
        paiement.reference_prestataire = f"SIM-{paiement.pk:06d}"
        paiement.paye_le = timezone.now()
        paiement.save()
        return Response({'detail': 'Paiement confirmé (simulation)', 'statut': paiement.statut})