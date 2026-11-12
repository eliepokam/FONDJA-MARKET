from decimal import Decimal
from django.db import models
from django.db.models import Q
from django.core.validators import MinValueValidator

# Create your models here.

class LotDedouanement(models.Model):
    """
    Conteneur (lot d'embarquement). Il est créé par un administrateur avec son numéro BESC, son type et sa capacité utile ; les colis y sont chargés
    à l'embarquement. Son pourcentage de remplissage = volume (m³) des colis qu'il contient / capacité utile.
    Le nom de la classe et de la table (« lots_dedouanement ») sont conservés pour ne pas casser l'existant ; seuls les libellés ont changé.
    """
    class Statut(models.TextChoices):
        OUVERT = 'ouvert', 'En chargement'
        CLOTURE = 'cloture', 'Clôturé'

    class TypeConteneur(models.TextChoices):
        VINGT = '20', "Conteneur 20 pieds"
        QUARANTE = '40', "Conteneur 40 pieds"
        QUARANTE_HC = '40hc', "Conteneur 40 pieds High Cube"
        AUTRE = 'autre', "Autre capacité"

    # Capacités utiles indicatives (m³) ; l'administrateur peut les ajuster à la création ou ensuite.
    CAPACITES_STANDARD = {'20': Decimal('33'), '40': Decimal('67'), '40hc': Decimal('76')}

    numero_besc = models.CharField(max_length=50)
    administrateur = models.ForeignKey('users.Administrateur', on_delete=models.SET_NULL, null=True, related_name='lots_dedouanement')
    cree_le = models.DateTimeField(auto_now_add=True)
    type_conteneur = models.CharField(max_length=5, choices=TypeConteneur.choices, default=TypeConteneur.VINGT)
    capacite_m3 = models.DecimalField(max_digits=7, decimal_places=2, default=Decimal('33'), validators=[MinValueValidator(Decimal('0.1'))])
    statut = models.CharField(max_length=10, choices=Statut.choices, default=Statut.OUVERT)
    cloture_le = models.DateTimeField(null=True, blank=True)
    cloture_par = models.ForeignKey('users.Administrateur', on_delete=models.SET_NULL, null=True, blank=True, related_name='lots_clotures')

    class Meta:
        db_table = 'lots_dedouanement'
        verbose_name = "conteneur (lot d'embarquement)"
        verbose_name_plural = "conteneurs (lots d'embarquement)"

    def __str__(self):
        return self.numero_besc


class RemplissageLot(models.Model):
    """Photographie du remplissage d'un conteneur, prise à chaque changement (colis ajouté, retiré, mesuré, capacité modifiée) : c'est son « évolution »."""
    lot = models.ForeignKey(LotDedouanement, on_delete=models.CASCADE, related_name='remplissages')
    volume_m3 = models.DecimalField(max_digits=9, decimal_places=3, default=Decimal('0'))
    pourcentage = models.DecimalField(max_digits=7, decimal_places=2, default=Decimal('0'))
    nb_colis = models.PositiveIntegerField(default=0)
    cree_le = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'remplissages_lot'
        ordering = ['cree_le', 'id']
        indexes = [models.Index(fields=['lot', 'cree_le'])]

    def __str__(self):
        return f"{self.lot_id} : {self.pourcentage} %"


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
    poids_declare_kg = models.DecimalField(max_digits=8, decimal_places=2, null=True, blank=True, validators=[MinValueValidator(0)])
    volume_declare_m3 = models.DecimalField(max_digits=8, decimal_places=3, null=True, blank=True, validators=[MinValueValidator(0)])
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
    delai_min_jours = models.PositiveSmallIntegerField(null=True, blank=True)
    delai_max_jours = models.PositiveSmallIntegerField(null=True, blank=True)
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


class AdresseTransit(models.Model):
    """
    Adresse de l'entrepôt en Chine que le client donne à son fournisseur, selon le mode de transport (une par mode).
    Modifiable par un administrateur depuis l'application : aucune adresse n'est codée en dur dans le programme.
    """
    class Mode(models.TextChoices):
        AERIEN = 'aerien', 'Transport aérien'
        MARITIME = 'maritime', 'Transport maritime'

    mode = models.CharField(max_length=10, choices=Mode.choices, unique=True)
    titre = models.CharField(max_length=100)
    adresse = models.TextField(help_text="Adresse exactement telle que le fournisseur doit l'écrire (en chinois).")
    destinataire = models.CharField(max_length=150, blank=True, help_text="Nom du destinataire ou de la société réceptrice.")
    telephone = models.CharField(max_length=40, blank=True)
    marquage = models.CharField(max_length=60, blank=True, help_text="Code ou marque à ajouter sur le colis (ex. « Mary777 »).")
    instructions = models.TextField(blank=True, help_text="Conseils affichés au client, en français.")
    actif = models.BooleanField(default=True)
    modifie_le = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'adresses_transit'
        ordering = ['mode']

    def __str__(self):
        return f"{self.get_mode_display()} — {self.titre}"

    @property
    def texte_a_copier(self):
        """Texte complet à envoyer au fournisseur, dans le format habituel des adresses de réception en Chine."""
        texte = self.adresse.strip()
        if self.mode == self.Mode.AERIEN:
            if self.destinataire:
                texte += f"  收货人：{self.destinataire}"
            if self.telephone:
                texte += f" 电话：{self.telephone}"
            return texte
        if self.destinataire:
            texte += f"，{self.destinataire}"
        if self.marquage:
            texte += f"  + {self.marquage}"
        return texte
