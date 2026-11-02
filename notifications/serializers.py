from rest_framework import serializers
from .models import Notification
from django.utils import timezone
 
class NotificationSerializer(serializers.ModelSerializer):
    class Meta:
        model = Notification
        fields = '__all__'

def notify(utilisateur, message: str, type_: str, colis=None, canal=Notification.Canal.PUSH):
    Notification.objects.create(
        utilisateur=utilisateur,
        colis=colis,
        type=type_,
        canal=canal,
        contenu=message,
        envoye_le=timezone.now(),
    )

def notify_staff(message: str, type_: str, colis=None):
    """Notifie tous les agents et administrateurs — utile tant qu'aucune réclamation
    n'est encore assignée à une personne précise."""
    from users.models import Agent, Administrateur
    for agent in Agent.objects.all():
        notify(agent, message, type_, colis=colis)
    for admin in Administrateur.objects.all():
        notify(admin, message, type_, colis=colis)