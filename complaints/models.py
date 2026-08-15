from django.db import models

# Create your models here.

class Reclamation(models.Model):
    class Type(models.TextChoices):
        COLIS_ENDOMMAGE = 'colis_endommage', 'Colis endommagé'
        RETARD = 'retard', 'Retard'
        DISPARU = 'disparu', 'Disparu'
        AUTRE = 'autre', 'Autre'
    class Statut(models.TextChoices):
        OUVERTE = 'ouverte', 'Ouverte'
        EN_COURS = 'en_cours', 'En cours'
        RESOLUE = 'resolue', 'Résolue'
        REJETEE = 'rejetee', 'Rejetée'

    colis = models.ForeignKey('shipments.Colis', on_delete=models.CASCADE, related_name='reclamations')
    client = models.ForeignKey('users.Client', on_delete=models.RESTRICT, related_name='reclamations')
    type = models.CharField(max_length=20, choices=Type.choices)
    description = models.TextField()
    photo_url = models.CharField(max_length=255, null=True, blank=True)
    statut = models.CharField(max_length=20, choices=Statut.choices, default=Statut.OUVERTE)
    # Volontairement vers Utilisateur (pas Agent) : un admin peut aussi traiter — cf. commentaire du schéma
    agent_assigne = models.ForeignKey(
        'users.Utilisateur', on_delete=models.SET_NULL, null=True, blank=True, related_name='reclamations_assignees'
    )
    cree_le = models.DateTimeField(auto_now_add=True)
    modifie_le = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'reclamations'
        ordering = ['-cree_le']
        indexes = [models.Index(fields=['colis']), models.Index(fields=['statut'])]

    def __str__(self):
        return f"Réclamation #{self.pk} — {self.get_statut_display()}"


class MessageReclamation(models.Model):
    reclamation = models.ForeignKey(Reclamation, on_delete=models.CASCADE, related_name='messages')
    expediteur = models.ForeignKey('users.Utilisateur', on_delete=models.CASCADE, related_name='messages_reclamation')
    message = models.TextField()
    cree_le = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'messages_reclamation'
        indexes = [models.Index(fields=['reclamation', 'cree_le'])]