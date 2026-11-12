"""Tient à jour l'historique de remplissage des conteneurs quand un colis y entre, en sort, change de volume ou est supprimé."""
from django.db.models.signals import post_delete, post_save, pre_save
from django.dispatch import receiver

from .conteneurs import enregistrer_pour
from .models import Colis

CHAMPS_SUIVIS = ('lot_id', 'volume_m3', 'volume_declare_m3')


@receiver(pre_save, sender=Colis)
def memoriser_avant(sender, instance, **kwargs):
    instance._avant_lot = (Colis.objects.filter(pk=instance.pk).values(*CHAMPS_SUIVIS).first() if instance.pk else None)


@receiver(post_save, sender=Colis)
def apres_enregistrement(sender, instance, created, **kwargs):
    avant = getattr(instance, '_avant_lot', None)
    if avant is not None and all(avant[c] == getattr(instance, c) for c in CHAMPS_SUIVIS):
        return                                        # un simple changement de statut ne modifie pas le remplissage
    enregistrer_pour([instance.lot_id, (avant or {}).get('lot_id')])


@receiver(post_delete, sender=Colis)
def apres_suppression(sender, instance, **kwargs):
    enregistrer_pour([instance.lot_id])
