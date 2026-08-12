from django.contrib.auth.base_user import AbstractBaseUser, BaseUserManager
from django.contrib.auth.hashers import make_password
from django.contrib.auth.models import PermissionsMixin
from phonenumber_field.modelfields import PhoneNumberField
from django.db import models


class UtilisateurManager(BaseUserManager):
    def create_utilisateur(self, telephone, nom, password=None, **extra):
        if not telephone:
            raise ValueError("Le téléphone est obligatoire.")
        utilisateur = self.model(telephone=telephone, nom=nom, **extra)
        utilisateur.password = make_password(password)
        utilisateur.save(using=self._db)
        return utilisateur

    # Django appelle ces deux méthodes précises via createsuperuser — noms imposés, pas de liberté ici
    def create_user(self, telephone, nom, password=None, **extra):
        return self.create_utilisateur(telephone, nom, password, **extra)

    def create_superuser(self, telephone, nom, password=None, **extra):
        extra.setdefault('is_superuser', True)
        return self.create_utilisateur(telephone, nom, password, **extra)


class Utilisateur(AbstractBaseUser, PermissionsMixin):
    class Statut(models.TextChoices):
        ACTIF = 'actif', 'Actif'
        DESACTIVE = 'desactive', 'Désactivé'

    nom = models.CharField(max_length=150)
    telephone = PhoneNumberField(unique=True)
    email = models.EmailField(max_length=150, null=True, blank=True)
    statut = models.CharField(max_length=10, choices=Statut.choices, default=Statut.ACTIF)
    telephone_verifie_le = models.DateTimeField(null=True, blank=True)
    jeton_connexion = models.CharField(max_length=100, null=True, blank=True)
    cree_le = models.DateTimeField(auto_now_add=True)
    modifie_le = models.DateTimeField(auto_now=True)
    # is_superuser : SUPPRIME d'ici, fourni maintenant par PermissionsMixin

    USERNAME_FIELD = 'telephone'
    REQUIRED_FIELDS = ['nom']

    objects = UtilisateurManager()

    @property
    def is_active(self):
        return self.statut == self.Statut.ACTIF

    @property
    def is_staff(self):
        return self.is_superuser or hasattr(self, 'administrateur')

    class Meta:
        db_table = 'utilisateurs'

    def __str__(self):
        return f"{self.nom} ({self.telephone})"

class Client(Utilisateur):
    utilisateur_ptr = models.OneToOneField(
        Utilisateur, on_delete=models.CASCADE, parent_link=True,
        primary_key=True, db_column='id_utilisateur', related_name='client'
    )
    code_client = models.CharField(max_length=20, unique=True)

    class Meta:
        db_table = 'clients'


class Administrateur(Utilisateur):
    utilisateur_ptr = models.OneToOneField(
        Utilisateur, on_delete=models.CASCADE, parent_link=True,
        primary_key=True, db_column='id_utilisateur', related_name='administrateur'
    )

    class Meta:
        db_table = 'administrateurs'


class Agent(Utilisateur):
    class Localisation(models.TextChoices):
        CHINE = 'chine', 'Chine'
        CAMEROUN = 'cameroun', 'Cameroun'

    utilisateur_ptr = models.OneToOneField(
        Utilisateur, on_delete=models.CASCADE, parent_link=True,
        primary_key=True, db_column='id_utilisateur', related_name='agent'
    )
    localisation = models.CharField(max_length=20, choices=Localisation.choices, default=Localisation.CHINE)
    cree_par = models.ForeignKey(Administrateur, on_delete=models.RESTRICT, related_name='agents_crees')
    supprime_par = models.ForeignKey(
        Administrateur, on_delete=models.RESTRICT, null=True, blank=True, related_name='agents_supprimes'
    )
    supprime_le = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = 'agents'


class CodeOtp(models.Model):
    class Canal(models.TextChoices):
        SMS = 'sms', 'SMS'
        WHATSAPP = 'whatsapp', 'WhatsApp'

    utilisateur = models.ForeignKey(Utilisateur, on_delete=models.CASCADE, related_name='codes_otp')
    code = models.CharField(max_length=6)
    canal = models.CharField(max_length=10, choices=Canal.choices, default=Canal.SMS)
    expire_le = models.DateTimeField()
    verifie_le = models.DateTimeField(null=True, blank=True)
    tentatives = models.PositiveSmallIntegerField(default=0)
    cree_le = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'codes_otp'