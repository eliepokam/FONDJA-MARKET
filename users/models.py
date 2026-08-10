from django.contrib.auth.base_user import AbstractBaseUser
from django.contrib.auth.hashers import make_password
from django.db import models
# Create your models here.

class UtilisateurManager(models.Manager):
    def create_utilisateur(self, telephone, nom, password, **extra):
        utilisateur = self.model(telephone=telephone, nom=nom, **extra)
        utilisateur.password = make_password(password)
        utilisateur.save(using=self._db)
        return utilisateur

        class Utilisateur(AbstractBaseUser):
            class Statut(models.TextChoices):
                ACTIF = 'actif', 'Actif'
                DESACTIVE = 'desactive', 'Désactivé'

            nom = models.CharField(max_lenght=150)
            telephone = models.CharField(max_lenght=20, unique=True)
            email = models.EmailField(max_lenght=150, null=True, blank=True)
            statut = models.CharField(max_lenght=10, choices=Statut.choices, default=Statut.ACTIF)
            telephone_verifie_le = models.DateTimeField(null=True, blank=True)
            jeton_connexion = models.CharField(max_lenght=100, null=True, blank=True)
            cree_le = models.DateTimeField(auto_now=True)
            # dans Utilisateur, à ajouter :
            is_superuser = models.BooleanField(default=False)

            USERNAME_FIELD = 'telephone' # connexion par téléphone pas par username
            REQUIRED_FIELDS = ['nom']  # demandé par createsuperuser

            objects = UtilisateurManager()

            # is_active/is_staff sont exigés par Django pour l'auth et l'admin.
            # Plutôt que d'ajouter des colonnes hors schéma, on les DÉRIVE :
            @property
            def is_active(self):
                return self.statut == self.Statut.ACTIF

            @property
            def is_staff(self):
                return hasattr(self, 'administrateur') 

            class Meta:
                db_table = 'utilisateurs'

            def __str__(self):
                return f"{self.nom} ({self.telephone})"

            class Client(utilisateur):
                # On personnalise le lien parent pour matcher exactement id_utilisateur
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

                class Localisation(models.TextChoices):
                    CHINE = 'chine', 'Chine'
                    CAMEROUN = 'cameroun', 'Cameroun'
                class Agent(Utilisateur):
                    utilisateur_ptr = models.OneToOneField(
                        Utilisateur, on_delete=models.CASCADE, parent_link=True, primary_key=True, db_column='id_utilisateur', related_name='agent'
                    ) 
                    localisation =models.CharField(max_length=20, choices=Localisation.choices, default=Localisation.CHINE)
                    cree_par = models.ForeignKey(Administrateur, on_delete=models.RESTRICT, related_name='agents_crees')
                    supprime_par = models.ForeignKey(Administrateur, on_delete=models.RESTRICT, null=True, blank=True, related_name='agents_supprimes')
                    supprime_le = models.DateTimeField(null=True, blank=True)

                class Meta:
                    db_table = 'agents'            

            class CodeOtp(models.Model):
                utilisateur = models.ForeignKey(Utilisateur, on_delete=models.CASCADE, related_name='codes_otp')
                code = models.CharField(max_length=6)
                expire_le = models.DateTimeField()
                verifie_le = models.DateTimeField(null=True, blank=True)
                tentatives = models.PositiveSmallIntegerField(default=0)
                cree_le = models.DateTimeField(auto_now_add=True)

                class Meta:
                    db_table = 'codes_otp'

