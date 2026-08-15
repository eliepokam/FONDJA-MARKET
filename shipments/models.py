from django.db import models
from django.db.models import Q
from django.core.validators import MinValueValidator

# Create your models here.

class LotDedouanement(models.Model):
    numero_besc = models.CharField(max_length=50)
    administrateur = models.ForeignKey('users.Administrateur', on_delete=models.SET_NULL, null=True, related_name='lots_dedouanement')
    cree_le = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'lots_dedouanement'

    def __str__(self):
        return self.numero_besc
    
class Colis(models.Model):
    class ModeTransport(models.TextChoices):
        AERIEN_EXPRESS = 'aerien_express', 'Aérien express'
        AERIEN_STANDARD = 'aerien_standard', 'Aérien standard'
        MARITIME = 'maritime', 'Maritime'
    class Statut(models.TextChoices):
        DECLAREE = 'declaree', 'Déclarée'
        RECU_ENTREPOT = 'recu_entrepot', 'Reçu à l\'entrepôt'
        EMBARQUE = 'embarque', 'Embarqué'
        EN_TRANSIT = 'en_transit', 'En transit'
        DEDOUANEMENT = 'dedouanement', 'Dédouanement'
        ARRIVE_DEPOT_LOCAL = 'arrive_depot_local', 'Arrivé au dépôt local'
        EN_LIVRAISON = 'en_livraison', 'En livraison'
        LIVRE = 'livre', 'Livré'
    client = models.ForeignKey('users.Client', on_delete=models.RESTRICT, related_name='colis')
    lot = models.ForeignKey(
        LotDedouanement, on_delete=models.SET_NULL, null=True, blank=True, related_name='colis'
    )
    code_suivi = models.CharField(max_length=30, unique=True)
    description = models.TextField(null=True, blank=True)
    poids_kg = models.DecimalField(max_digits=8, decimal_places=2, null=True, blank=True, validators=[MinValueValidator(0)])
    volume_m3 = models.DecimalField(max_digits=8, decimal_places=3, null=True, blank=True, validators=[MinValueValidator(0)])
    mode_transport = models.CharField(max_length=20, choices=ModeTransport.choices)
    photo_declaree_url = models.CharField(max_length=255, null=True, blank=True)
    statut = models.CharField(max_length=20, choices=Statut.choices, default=Statut.DECLAREE)
    cout_estime = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True, validators=[MinValueValidator(0)])
    cout_final = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True, validators=[MinValueValidator(0)])
    date_livraison_estimee = models.DateField(null=True, blank=True)
    numero_besc = models.CharField(max_length=50, null=True, blank=True)
    cree_le = models.DateTimeField(auto_now_add=True)
    modifie_le = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'colis'
        ordering = ['-cree_le']
        indexes = [
            models.Index(fields=['client']),
            models.Index(fields=['statut']),
            models.Index(fields=['statut', 'date_livraison_estimee']),
        ]

    def __str__(self):
        return self.code_suivi

class Tarif(models.Model):
    mode_transport = models.CharField(max_length=20, choices=Colis.ModeTransport.choices)
    prix_par_kg = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True, validators=[MinValueValidator(0)])
    prix_par_m3 = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True, validators=[MinValueValidator(0)])
    delai_estime_jours = models.PositiveSmallIntegerField(null=True, blank=True)
    devise = models.CharField(max_length=3, default='XAF')
    actif = models.BooleanField(default=True)
    administrateur = models.ForeignKey(
        'users.Administrateur', on_delete=models.SET_NULL, null=True, blank=True, related_name='tarifs'
    )
    cree_le = models.DateTimeField(auto_now_add=True)
    modifie_le = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'tarifs'

    def __str__(self):
        return f"{self.get_mode_transport_display()} ({self.devise})"

class Incident(models.Model):
    class Type(models.TextChoices):
        RETARD = 'retard', 'Retard'
        BLOCAGE_DOUANE = 'blocage_douane', 'Blocage douane'
        AVARIE = 'avarie', 'Avarie'
        PERTE_PARTIELLE = 'perte_partielle', 'Perte partielle'
        AUTRE = 'autre', 'Autre'

    class Gravite(models.TextChoices):
        INFORMATION = 'information', 'Information'
        IMPORTANT = 'important', 'Important'
        CRITIQUE = 'critique', 'Critique'

    colis = models.ForeignKey(Colis, on_delete=models.CASCADE, null=True, blank=True, related_name='incidents')
    lot = models.ForeignKey(LotDedouanement, on_delete=models.CASCADE, null=True, blank=True, related_name='incidents')
    type = models.CharField(max_length=20, choices=Type.choices)
    gravite = models.CharField(max_length=20, choices=Gravite.choices, default=Gravite.INFORMATION)
    description = models.TextField()
    declare_par = models.ForeignKey(
        'users.Utilisateur', on_delete=models.SET_NULL, null=True, blank=True, related_name='incidents_declares'
    )
    resolu_le = models.DateTimeField(null=True, blank=True)
    cree_le = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'incidents'
        constraints = [
            models.CheckConstraint(
                condition=(
                    Q(colis__isnull=False, lot__isnull=True) |
                    Q(colis__isnull=True, lot__isnull=False)
                ),
                name='chk_incident_cible'
            )
        ]
        indexes = [
            models.Index(fields=['colis']),
            models.Index(fields=['lot']),
            models.Index(fields=['resolu_le']),
        ]