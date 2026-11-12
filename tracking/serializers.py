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
    # « entrepot » = prise par l'équipe (agent) ; « client » = ajoutée par le client lui-même
    source = serializers.SerializerMethodField()

    def get_source(self, obj):
        return 'entrepot' if obj.agent_id else 'client'

    class Meta:
        model = PhotoColis
        fields = ['id', 'colis', 'type_media', 'photo', 'agent', 'source', 'prise_le', 'cree_le']
        read_only_fields = ['colis', 'agent', 'prise_le', 'cree_le']

    def validate(self, attrs):
        fichier = attrs['url']
        if attrs.get('type_media') == PhotoColis.TypeMedia.VIDEO:
            valider_video(fichier)
        else:
            valider_image(fichier)
        return attrs


class MediaClientSerializer(PhotoColisSerializer):
    """Média vu dans l'espace « Photos et vidéos » du client : on ajoute de quoi retrouver le colis concerné."""
    code_suivi = serializers.CharField(source='colis.code_suivi', read_only=True)
    colis_description = serializers.CharField(source='colis.description', read_only=True, allow_null=True)
    statut_colis = serializers.CharField(source='colis.statut', read_only=True)

    class Meta(PhotoColisSerializer.Meta):
        fields = PhotoColisSerializer.Meta.fields + ['code_suivi', 'colis_description', 'statut_colis']
