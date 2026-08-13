from django.shortcuts import get_object_or_404
from rest_framework import viewsets, generics, permissions

from .models import Reclamation
from .serializers import ReclamationSerializer, MessageReclamationSerializer
from users.permissions import IsClient

# Create your views here.

class ReclamationViewSet(viewsets.ModelViewSet):
    serializer_class = ReclamationSerializer

    def get_queryset(self):
        user = self.request.user
        if hasattr(user, 'client'):
            return Reclamation.objects.filter(client=user.client)
        return Reclamation.objects.all()  # administrateur/agent voient tout

    def perform_create(self, serializer):
        serializer.save(client=self.request.user.client)

    def get_permissions(self):
        if self.action == 'create':
            return [IsClient()]
        return [permissions.IsAuthenticated()]

class MessageReclamationCreateView(generics.CreateAPIView):
    serializer_class = MessageReclamationSerializer

    def perform_create(self, serializer):
        reclamation = get_object_or_404(Reclamation, pk=self.kwargs['reclamation_id'])
        serializer.save(reclamation=reclamation, expediteur=self.request.user)