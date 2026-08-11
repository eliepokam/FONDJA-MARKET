from django.db import models
from shipments.models import Colis
from users.models import Agent

# Create your models here.

class HistoriqueStatutColis(models.Model):
    colis = models.ForeignKey(Colis, on_delete=models.CASCADE, related_name='historique_statuts')
    statut = models.CharField(max_length=20, choices=Colis.Statut.choices)
    agent = models.ForeignKey(Agent, on_delete=models.RESTRICT, null=True, blank=True, related_name='changements_statuts_colis')
    note = models.CharField(max_length=255, null=True, blank=True)
    cree_le = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'historique_statut_colis'
        indexes = [models.Index(fields=['colis', 'cree_le'])]

class PhotoColis(models.Model):
    class TypeMedia(models.TextChoices):
        PHOTO = 'photo', 'Photo'
        VIDEO = 'video', 'Vidéo'

    colis = models.ForeignKey('shipments.Colis', on_delete=models.CASCADE, related_name='photos')
    historique_statut = models.ForeignKey(
        HistoriqueStatutColis, on_delete=models.SET_NULL, null=True, blank=True, related_name='photos'
    )
    type_media = models.CharField(max_length=10, choices=TypeMedia.choices, default=TypeMedia.PHOTO)
    url = models.CharField(max_length=255)
    agent = models.ForeignKey(
        'users.Agent', on_delete=models.RESTRICT, null=True, blank=True, related_name='photos_prises'
    )
    prise_le = models.DateTimeField()
    cree_le = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'photos_colis'
        indexes = [models.Index(fields=['colis'])]