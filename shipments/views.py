from rest_framework import viewsets, permissions
from .models import Colis, LotDedouanement, Tarif
from .serializers import ColisSerializer, LotDedouanementSerializer, TarifSerializer
from .utils import generate_code_suivi
from users.permissions import IsClient, IsAdministrateur, IsAgent


class ColisViewSet(viewsets.ModelViewSet):
    serializer_class = ColisSerializer

    def get_queryset(self):
        user = self.request.user
        if hasattr(user, 'client'):
            return Colis.objects.filter(client=user.client)
        return Colis.objects.all()   # administrateur/agent voient tout

    def perform_create(self, serializer):
        serializer.save(client=self.request.user.client, code_suivi=generate_code_suivi())

    def get_permissions(self):
        if self.action == 'create':
            return [IsClient()]
        return [permissions.IsAuthenticated()]


class LotDedouanementViewSet(viewsets.ModelViewSet):
    queryset = LotDedouanement.objects.all().order_by('-cree_le')
    serializer_class = LotDedouanementSerializer

    def perform_create(self, serializer):
        serializer.save(administrateur=self.request.user.administrateur)

    def get_permissions(self):
        if self.request.method in permissions.SAFE_METHODS:   # GET, HEAD, OPTIONS
            return [(IsAdministrateur | IsAgent)()]
        return [IsAdministrateur()]

class TarifViewSet(viewsets.ModelViewSet):
    serializer_class = TarifSerializer

    def get_queryset(self):
        user = self.request.user
        if hasattr(user, 'client'):
            return Tarif.objects.filter(actif=True) # le client ne voit que les tarifs en vigueur
        return Tarif.objects.all()  # administrateur/agent voient tout

    def perform_create(self, serializer):
        serializer.save(administrateur=self.request.user.administrateur)

    def get_permissions(self):
        if self.request.method in permissions.SAFE_METHODS:   # GET, HEAD, OPTIONS
            return [permissions.IsAuthenticated()]
        return [IsAdministrateur()]