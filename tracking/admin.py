from django.contrib import admin
from .models import HistoriqueStatutColis, PhotoColis

# Register your models here.
@admin.register(HistoriqueStatutColis)
class HistoriqueStatutColisAdmin(admin.ModelAdmin):
    list_display = ['colis', 'statut', 'cree_le']
    list_filter = ['statut']
    search_fields = ['colis__code_suivi']

@admin.register(PhotoColis)
class PhotoColisAdmin(admin.ModelAdmin):
    list_display = ['colis', 'type_media', 'agent', 'prise_le']
