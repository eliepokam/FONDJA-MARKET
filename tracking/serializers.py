from rest_framework import serializers
from core.validators import valider_image, valider_video
from shipments.models import Colis
from .models import PhotoColis 

class ChangerStatutSerializer(serializers.Serializer):
    statut = serializers.ChoiceField(choices=Colis.Statut.choices)
    note = serializers.CharField(required=False, allow_blank=True, max_length=255)

class PhotoColisSerializer(serializers.ModelSerializer):
    # FileField et non ImageField : l'agent peut aussi envoyer une VIDÉO du colis (« filmer 20-30 s »). Avec un ImageField,
    # chaque vidéo était refusée (« fichier non valide ») et l'envoi échouait sans que personne ne le voie.
    photo = serializers.FileField(source='url')

    class Meta:
        model = PhotoColis
        fields = ['id', 'colis', 'type_media', 'photo', 'agent', 'prise_le', 'cree_le']
        read_only_fields = ['colis', 'agent', 'prise_le', 'cree_le']

    def validate(self, attrs):
        fichier = attrs['url']
        if attrs.get('type_media') == PhotoColis.TypeMedia.VIDEO:
            valider_video(fichier)
        else:
            valider_image(fichier)
        return attrs
