from django.db import models
from django.db.models import Q
from decimal import Decimal
from django.core.validators import MinValueValidator
from phonenumber_field.modelfields import PhoneNumberField

from .storage import stockage_prive

# Create your models here.

class Paiement(models.Model):
    class Methode(models.TextChoices):
        MTN_MOMO = 'mtn_momo', 'MTN Mobile Money'
        ORANGE_MONEY = 'orange_money', 'Orange Money'
        VIREMENT = 'virement', 'Virement bancaire'
        # ANCIEN mode, supprimé : conservé uniquement pour l'historique et le chiffre d'affaires déjà encaissé. Refusé à la création.
        ESPECES = 'especes', 'Espèces à la livraison (supprimé)'

    class Moment(models.TextChoices):
        EN_LIGNE = 'en_ligne', 'En ligne'
        HORS_LIGNE = 'hors_ligne', 'Hors ligne (virement)'
        A_LA_LIVRAISON = 'a_la_livraison', 'À la livraison (supprimé)'

    class Statut(models.TextChoices):
        EN_ATTENTE = 'en_attente', 'En attente'
        PAYE = 'paye', 'Payé'
        ECHOUE = 'echoue', 'Échoué'
        REMBOURSE = 'rembourse', 'Remboursé'

    colis = models.ForeignKey('shipments.Colis', on_delete=models.RESTRICT, related_name='paiements')
    client = models.ForeignKey('users.Client', on_delete=models.RESTRICT, related_name='paiements')
    montant = models.DecimalField(max_digits=10, decimal_places=2, validators=[MinValueValidator(Decimal('0.01'))])
    devise = models.CharField(max_length=3, default='XAF')
    methode = models.CharField(max_length=20, choices=Methode.choices)
    moment = models.CharField(max_length=20, choices=Moment.choices)
    statut = models.CharField(max_length=20, choices=Statut.choices, default=Statut.EN_ATTENTE)
    # Numéro Mobile Money à débiter (saisi par le client, peut différer de son numéro de compte).
    telephone_paiement = PhoneNumberField(null=True, blank=True)
    reference_prestataire = models.CharField(max_length=100, null=True, blank=True)
    # ── Virement bancaire : preuve envoyée par le client, vérifiée par un agent ou un administrateur ──
    preuve = models.FileField(upload_to='preuves/%Y/%m', storage=stockage_prive, null=True, blank=True)
    preuve_envoyee_le = models.DateTimeField(null=True, blank=True)
    reference_virement = models.CharField(max_length=100, blank=True, help_text="Référence de l'opération indiquée par le client.")
    verifie_par = models.ForeignKey('users.Utilisateur', null=True, blank=True, on_delete=models.SET_NULL, related_name='virements_verifies')
    verifie_le = models.DateTimeField(null=True, blank=True)
    motif_refus = models.CharField(max_length=300, blank=True)
    paye_le = models.DateTimeField(null=True, blank=True)
    cree_le = models.DateTimeField(auto_now_add=True)
    modifie_le = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'paiements'
        ordering = ['-cree_le']
        constraints = [
            models.CheckConstraint(
                condition=~Q(methode='especes', moment='en_ligne'),
                name='chk_paiement_coherent'
            ),
            models.CheckConstraint(
                condition=~Q(methode='virement') | Q(moment='hors_ligne'),
                name='chk_paiement_virement_hors_ligne'
            ),
        ]
        indexes = [
            models.Index(fields=['colis']),
            models.Index(fields=['client']),
            models.Index(fields=['statut']),
        ]

    def __str__(self):
        return f"{self.montant} {self.devise} — {self.get_statut_display()}"


class CompteBancaire(models.Model):
    """Compte sur lequel le client fait son virement. Géré par l'administrateur : le client ne voit que les comptes actifs."""
    banque = models.CharField(max_length=120)
    titulaire = models.CharField(max_length=150)
    numero_compte = models.CharField(max_length=60, help_text="Numéro de compte ou RIB.")
    iban = models.CharField(max_length=40, blank=True)
    code_swift = models.CharField(max_length=20, blank=True)
    agence = models.CharField(max_length=120, blank=True)
    instructions = models.TextField(blank=True, help_text="Consignes affichées au client (ex. indiquer le code du colis en motif).")
    actif = models.BooleanField(default=True)
    ordre = models.PositiveSmallIntegerField(default=0)
    cree_le = models.DateTimeField(auto_now_add=True)
    modifie_le = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'comptes_bancaires'
        ordering = ['ordre', 'id']

    def __str__(self):
        return f"{self.banque} — {self.titulaire}"
