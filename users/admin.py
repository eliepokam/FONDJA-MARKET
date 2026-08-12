from django.contrib import admin
from django.contrib.auth.hashers import make_password
from .models import Administrateur, Utilisateur, Client, Agent, CodeOtp


# Register your models here.

class HashPasswordAdminMixin:
    """
    Le champ 'password' d'AbstractBaseUser s'affiche en texte brut dans
    l'admin par défaut. Sans ce mixin, taper un mot de passe ici l'enregistrerait
    tel quel (non hashé) -> login impossible ensuite. On le hash uniquement
    si l'admin l'a réellement modifié (form.changed_data), sinon on double-hash
    un hash déjà existant lors d'une simple édition.
    """

    def save_model(self, request, obj, form, change):
        if 'password' in form.changed_data:
            obj.password = make_password(form.cleaned_data['password'])
        super().save_model(request, obj, form, change)

@admin.register(Utilisateur)
class UtilisateurAdmin(HashPasswordAdminMixin, admin.ModelAdmin):
    list_display = ['nom', 'telephone', 'email', 'statut', 'is_superuser']
    search_fields = ['nom', 'telephone']

@admin.register(Client)
class ClientAdmin(HashPasswordAdminMixin, admin.ModelAdmin):
    list_display = ['nom', 'telephone', 'code_client']

@admin.register(Administrateur)
class AdministrateurAdmin(HashPasswordAdminMixin, admin.ModelAdmin):
    list_display = ['nom', 'telephone', 'email']

@admin.register(Agent)
class AgentAdmin(HashPasswordAdminMixin, admin.ModelAdmin):
    list_display = ['nom', 'telephone', 'localisation', 'cree_par']

@admin.register(CodeOtp)
class CodeOtpAdmin(admin.ModelAdmin):
    list_display = ['utilisateur', 'code', 'canal', 'verifie_le', 'expire_le']
