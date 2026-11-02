from django.db import models
from users.models import Utilisateur

# Create your models here.

class Notification(models.Model):
    class Type(models.TextChoices):
        STATUT_COLIS = 'statut_colis', 'Statut du colis'
        PAIEMENT = 'paiement', 'Paiement'
        RECLAMATION = 'reclamation', 'Réclamation'
        INCIDENT = 'incident', 'Incident'
        GENERAL = 'general', 'Général'

    class Canal(models.TextChoices):
        SMS = 'sms', 'SMS'
        PUSH = 'push', 'Push'
        EMAIL = 'email', 'Email'
        
    utilisateur = models.ForeignKey('users.Utilisateur', on_delete=models.CASCADE, related_name='notifications')
    colis = models.ForeignKey(
        'shipments.Colis', on_delete=models.SET_NULL, null=True, blank=True, related_name='notifications'
    )
    type = models.CharField(max_length=20, choices=Type.choices)
    canal = models.CharField(max_length=10, choices=Canal.choices)
    contenu = models.CharField(max_length=255)
    envoye_le = models.DateTimeField(null=True, blank=True)
    lu_le = models.DateTimeField(null=True, blank=True)
    cree_le = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'notifications'
        indexes = [models.Index(fields=['utilisateur', 'lu_le'])]

class FCMToken(models.Model):
    utilisateur = models.ForeignKey(Utilisateur, on_delete=models.CASCADE, related_name='fcm_tokens')
    token = models.CharField(max_length=255, unique=True)
    cree_le = models.DateTimeField(auto_now_add=True)