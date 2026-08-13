from django.contrib import admin
from .models import Colis, LotDedouanement, Tarif, Incident

# Register your models here.
@admin.register(Colis)
class ColisAdmin(admin.ModelAdmin):
    list_display = ['code_suivi', 'client', 'statut', 'mode_transport', 'cree_le']
    list_filter = ['statut', 'mode_transport']
    search_fields = ['code_suivi', 'client__nom', 'client__telephone']
    readonly_fields = ['code_suivi', 'cree_le', 'modifie_le']

@admin.register(LotDedouanement)
class LotDedouanementAdmin(admin.ModelAdmin):
    list_display = ['numero_besc', 'administrateur', 'cree_le']
    search_fields = ['numero_besc']

@admin.register(Tarif)
class TarifAdmin(admin.ModelAdmin):
    list_display = ['mode_transport', 'prix_par_kg', 'prix_par_m3', 'actif', 'devise']
    list_filter = ['mode_transport', 'actif']

@admin.register(Incident)
class IncidentAdmin(admin.ModelAdmin):
    list_display = ['type', 'gravite', 'colis', 'lot', 'resolu_le']
    search_fields = ['type', 'gravite']
