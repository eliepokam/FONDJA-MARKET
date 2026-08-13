from django.contrib import admin
from .models import Reclamation, MessageReclamation

# Register your models here.

class MessageReclamationInline(admin.TabularInline):
    model = MessageReclamation
    extra = 0
    readonly_fields = ['expediteur', 'cree_le']

@admin.register(Reclamation)
class ReclamationAdmin(admin.ModelAdmin):
    list_display = ['id', 'client', 'colis', 'type', 'statut', 'agent_assigne', 'cree_le']
    list_filter = ['statut', 'type']
    search_fields = ['client__nom', 'colis__code_suivi']
    inlines = [MessageReclamationInline]
