from django.shortcuts import get_object_or_404
from rest_framework import viewsets, generics, permissions

from .models import Reclamation, PhotoReclamation, PhotoMessageReclamation, MessageReclamation
from .serializers import ReclamationSerializer, ReclamationStaffSerializer, MessageReclamationSerializer, PhotoReclamationSerializer
from users.permissions import IsClient, IsAgent, IsAdministrateur
from core.validators import valider_image
from rest_framework.parsers import MultiPartParser, FormParser
from rest_framework.exceptions import PermissionDenied
from notifications.services import notify, notify_staff
from notifications.models import Notification

class ReclamationViewSet(viewsets.ModelViewSet):
    serializer_class = ReclamationSerializer
    filterset_fields = ['statut', 'type']      # le personnel trie ses réclamations par statut

    def get_queryset(self):
        user = self.request.user
        if hasattr(user, 'client'):
            return Reclamation.objects.filter(client=user.client)
        return Reclamation.objects.all()

    def perform_create(self, serializer):
        reclamation = serializer.save(client=self.request.user.client)
        notify_staff(
            f"Nouvelle réclamation de {reclamation.client.nom} : {reclamation.get_type_display()}",
            Notification.Type.RECLAMATION,
            colis=reclamation.colis,
        )

    def get_serializer_class(self):
        if self.action in ('update', 'partial_update'):
            return ReclamationStaffSerializer
        return ReclamationSerializer

    def perform_update(self, serializer):
        ancien_statut = serializer.instance.statut
        reclamation = serializer.save()
        if reclamation.statut == ancien_statut:
            return
        # Prise en charge : celui qui la passe « en cours » en devient responsable si personne ne l'est encore.
        if reclamation.statut == Reclamation.Statut.EN_COURS and reclamation.agent_assigne_id is None:
            reclamation.agent_assigne = self.request.user
            reclamation.save(update_fields=['agent_assigne', 'modifie_le'])
        messages = {
            Reclamation.Statut.EN_COURS: "Votre réclamation est prise en charge.",
            Reclamation.Statut.RESOLUE: "Votre réclamation a été résolue.",
            Reclamation.Statut.REJETEE: "Votre réclamation a été rejetée. Consultez la réponse du support.",
            Reclamation.Statut.OUVERTE: "Votre réclamation a été rouverte.",
        }
        notify(reclamation.client, f"Réclamation #{reclamation.pk} : {messages[reclamation.statut]}",
               Notification.Type.RECLAMATION, colis=reclamation.colis)

    def get_permissions(self):
        if self.action == 'create':
            return [IsClient()]
        if self.action in ('update', 'partial_update'):
            return [(IsAgent | IsAdministrateur)()]       # un client ne modifie jamais sa réclamation après coup
        if self.action == 'destroy':
            return [IsAdministrateur()]
        return [permissions.IsAuthenticated()]


class MessageReclamationCreateView(generics.CreateAPIView):
    serializer_class = MessageReclamationSerializer
    parser_classes = [MultiPartParser, FormParser]

    def perform_create(self, serializer):
        reclamation = get_object_or_404(Reclamation, pk=self.kwargs['reclamation_id'])
        sender = self.request.user

        is_staff = hasattr(sender, 'agent') or hasattr(sender, 'administrateur')
        is_owner = hasattr(sender, 'client') and reclamation.client_id == sender.client.id
        if not (is_staff or is_owner):
            raise PermissionDenied("Vous n'avez pas accès à cette réclamation.")
        if not is_staff and reclamation.statut in (Reclamation.Statut.RESOLUE, Reclamation.Statut.REJETEE):
            raise PermissionDenied("Cette réclamation est clôturée : ouvre-en une nouvelle si besoin.")

        photo = self.request.FILES.get('photo')
        if photo:
            valider_image(photo, 'photo')     # contrôle AVANT d'enregistrer le message : rien n'est créé si l'image est refusée

        message = serializer.save(reclamation=reclamation, expediteur=sender)
        if photo:
            PhotoMessageReclamation.objects.create(message=message, url=photo)

        if hasattr(sender, 'client'):
            if reclamation.agent_assigne:
                notify(reclamation.agent_assigne, f"Nouveau message sur la réclamation #{reclamation.pk}", Notification.Type.RECLAMATION, colis=reclamation.colis)
            else:
                notify_staff(f"Nouveau message sur la réclamation #{reclamation.pk}", Notification.Type.RECLAMATION, colis=reclamation.colis)
        else:
            notify(reclamation.client, "Le support a répondu à votre réclamation", Notification.Type.RECLAMATION, colis=reclamation.colis)
            
class PhotoReclamationCreateView(generics.ListCreateAPIView):
    serializer_class = PhotoReclamationSerializer
    parser_classes = [MultiPartParser, FormParser]

    def _verifier_acces(self, reclamation):
        user = self.request.user
        is_staff = hasattr(user, 'agent') or hasattr(user, 'administrateur')
        is_owner = hasattr(user, 'client') and reclamation.client_id == user.client.id
        if not (is_staff or is_owner):
            raise PermissionDenied("Vous n'avez pas accès à cette réclamation.")

    def get_queryset(self):
        # Sans ce contrôle, n'importe quel utilisateur connecté pouvait lister les photos de n'importe quelle réclamation.
        reclamation = get_object_or_404(Reclamation, pk=self.kwargs['reclamation_id'])
        self._verifier_acces(reclamation)
        return PhotoReclamation.objects.filter(reclamation=reclamation)

    def perform_create(self, serializer):
        reclamation = get_object_or_404(Reclamation, pk=self.kwargs['reclamation_id'])
        user = self.request.user

        is_staff = hasattr(user, 'agent') or hasattr(user, 'administrateur')
        is_owner = hasattr(user, 'client') and reclamation.client_id == user.client.id
        if not (is_staff or is_owner):
            raise PermissionDenied("Vous n'avez pas le droit d'ajouter une photo à cette réclamation.")

        serializer.save(reclamation=reclamation)