from django.contrib import admin
from .models import Paiement

# Register your models here.
@admin.register(Paiement)
class PaiementAdmin(admin.ModelAdmin):
    list_display = ['client', 'colis', 'montant', 'devise', 'methode', 'paye_le', 'statut']
    list_filter = ['statut', 'methode', 'moment']
    search_fields = ['client__nom', 'colis__code_suivi', 'reference_prestataire']
