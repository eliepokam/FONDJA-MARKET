from django.contrib import admin, messages
from django.utils import timezone

from .models import CompteBancaire, Paiement


# Register your models here.
@admin.register(Paiement)
class PaiementAdmin(admin.ModelAdmin):
    list_display = ['client', 'colis', 'montant', 'devise', 'methode', 'paye_le', 'statut']
    # La preuve de virement est privée (servie uniquement par l'API, après contrôle des droits) : pas de lien direct dans l'administration.
    exclude = ['preuve']
    readonly_fields = ['verifie_par', 'verifie_le']
    list_filter = ['statut', 'methode', 'moment']
    search_fields = ['client__nom', 'colis__code_suivi', 'reference_prestataire']
    actions = ['liberer_paiements_en_attente']

    @admin.action(description="Libérer le colis : marquer les paiements « en attente » comme échoués")
    def liberer_paiements_en_attente(self, request, queryset):
        """Débloque un colis dont le paiement est resté coincé, pour que le client puisse réessayer."""
        cibles = queryset.filter(statut=Paiement.Statut.EN_ATTENTE)
        avec_reference = cibles.exclude(reference_prestataire__isnull=True).exclude(reference_prestataire='').count()
        nombre = cibles.update(statut=Paiement.Statut.ECHOUE, modifie_le=timezone.now())
        self.message_user(request, f"{nombre} paiement(s) marqué(s) comme échoué(s).", messages.SUCCESS)
        if avec_reference:
            self.message_user(
                request,
                f"Attention : {avec_reference} d'entre eux avaient une référence prestataire. Vérifie dans le tableau de bord "
                f"du prestataire qu'aucun argent n'a été débité avant de laisser le client réessayer.",
                messages.WARNING,
            )


@admin.register(CompteBancaire)
class CompteBancaireAdmin(admin.ModelAdmin):
    list_display = ['banque', 'titulaire', 'actif', 'ordre']
    list_filter = ['actif']
