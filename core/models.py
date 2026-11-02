from django.db import models

class JournalActivite(models.Model):
    class Action(models.TextChoices):
        SUSPENSION_COMPTE = 'suspension_compte', 'Suspension de compte'
        REACTIVATION_COMPTE = 'reactivation_compte', 'Réactivation de compte'
        SUPPRESSION_COMPTE = 'suppression_compte', 'Suppression de compte'
        CREATION_STAFF = 'creation_staff', 'Création de compte staff'
        MODIFICATION_TARIF = 'modification_tarif', 'Modification de tarif'
        CREATION_TARIF = 'creation_tarif', 'Création de tarif'
        SUPPRESSION_TARIF = 'suppression_tarif', 'Suppression de tarif'

    auteur = models.ForeignKey('users.Administrateur', on_delete=models.SET_NULL, null=True, related_name='actions_journal')
    action = models.CharField(max_length=30, choices=Action.choices)
    cible_description = models.CharField(max_length=255)
    details = models.JSONField(default=dict, blank=True)
    cree_le = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'journal_activite'
        ordering = ['-cree_le']