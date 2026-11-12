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
        CREATION_LOT = 'creation_lot', 'Création de conteneur'
        SUPPRESSION_LOT = 'suppression_lot', 'Suppression de conteneur'
        MODIFICATION_LOT = 'modification_lot', 'Modification de conteneur'
        CLOTURE_LOT = 'cloture_lot', 'Clôture de conteneur'
        REOUVERTURE_LOT = 'reouverture_lot', 'Réouverture de conteneur'
        EXPORT_DONNEES = 'export_donnees', 'Export de données'
        PURGE_DONNEES = 'purge_donnees', 'Purge automatique des données'
        MODIFICATION_ADRESSE_TRANSIT = 'modification_adresse_transit', 'Modification d\'une adresse de transit'
        VALIDATION_VIREMENT = 'validation_virement', 'Validation d\'un virement'
        REFUS_VIREMENT = 'refus_virement', 'Refus d\'un virement'
        MODIFICATION_COMPTE_BANCAIRE = 'modification_compte_bancaire', 'Modification d\'un compte bancaire'

    auteur = models.ForeignKey('users.Administrateur', on_delete=models.SET_NULL, null=True, related_name='actions_journal')
    action = models.CharField(max_length=30, choices=Action.choices)
    cible_description = models.CharField(max_length=255)
    details = models.JSONField(default=dict, blank=True)
    cree_le = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'journal_activite'
        ordering = ['-cree_le']