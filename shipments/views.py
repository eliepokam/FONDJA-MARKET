from rest_framework import viewsets, permissions
from .models import Colis
from .serializers import ColisSerializer
from .utils import generate_code_suivi
from users.permissions import IsClient


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