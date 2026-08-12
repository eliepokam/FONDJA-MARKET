from rest_framework import serializers
from shipments.models import Colis
from .models import PhotoColis 

class ChangerStatutSerializer(serializers.Serializer):
    statut = serializers.ChoiceField(choices=Colis.Statut.choices)
    note = serializers.CharField(required=False, allow_blank=True)

class PhotoColisSerializer(serializers.ModelSerializer):
    photo = serializers.ImageField(source='url')

    class Meta:
        model = PhotoColis
        fields = ['id', 'colis', 'type_media', 'photo', 'agent', 'prise_le', 'cree_le']
        read_only_fields = ['colis', 'agent', 'prise_le', 'cree_le']