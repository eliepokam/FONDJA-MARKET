from django.db import models
from django.db.models import Q

# Create your models here.

class Paiement(models.Model):
    class Methode(models.TextChoices):
        MTN_MOMO = 'mtn_momo', 'MTN Mobile Money'
        ORANGE_MONEY = 'orange_money', 'Orange Money'
        ESPECES = 'especes', 'Espèces'

    class Moment(models.TextChoices):
        EN_LIGNE = 'en_ligne', 'En ligne'
        A_LA_LIVRAISON = 'a_la_livraison', 'À la livraison'

    class Statut(models.TextChoices):
        EN_ATTENTE = 'en_attente', 'En attente'
        PAYE = 'paye', 'Payé'
        ECHOUE = 'echoue', 'Échoué'
        REMBOURSE = 'rembourse', 'Remboursé'

    colis = models.ForeignKey('shipments.Colis', on_delete=models.RESTRICT, related_name='paiements')
    client = models.ForeignKey('users.Client', on_delete=models.RESTRICT, related_name='paiements')
    montant = models.DecimalField(max_digits=10, decimal_places=2)
    devise = models.CharField(max_length=3, default='XAF')
    methode = models.CharField(max_length=20, choices=Methode.choices)
    moment = models.CharField(max_length=20, choices=Moment.choices)
    statut = models.CharField(max_length=20, choices=Statut.choices, default=Statut.EN_ATTENTE)
    reference_prestataire = models.CharField(max_length=100, null=True, blank=True)
    paye_le = models.DateTimeField(null=True, blank=True)
    cree_le = models.DateTimeField(auto_now_add=True)
    modifie_le = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'paiements'
        constraints = [
            models.CheckConstraint(
                condition=~Q(methode='especes', moment='en_ligne'),
                name='chk_paiement_coherent'
            )
        ]
        indexes = [
            models.Index(fields=['colis']),
            models.Index(fields=['client']),
            models.Index(fields=['statut']),
        ]

    def __str__(self):
        return f"{self.montant} {self.devise} — {self.get_statut_display()}"