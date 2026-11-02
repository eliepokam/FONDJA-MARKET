from rest_framework import serializers
from core.validators import valider_image
from .models import PhotoMessageReclamation, Reclamation, MessageReclamation, PhotoReclamation


class PhotoMessageReclamationSerializer(serializers.ModelSerializer):
    class Meta:
        model = PhotoMessageReclamation
        fields = '__all__'
        read_only_fields = ['message', 'cree_le']


class MessageReclamationSerializer(serializers.ModelSerializer):
    sender_name = serializers.CharField(source='expediteur.nom', read_only=True)
    photos = PhotoMessageReclamationSerializer(many=True, read_only=True)

    class Meta:
        model = MessageReclamation
        fields = '__all__'
        read_only_fields = ['reclamation', 'expediteur', 'cree_le']
        
class PhotoReclamationSerializer(serializers.ModelSerializer):
    class Meta:
        model = PhotoReclamation
        fields = '__all__'
        read_only_fields = ['reclamation', 'cree_le']

    def validate_url(self, fichier):
        return valider_image(fichier)

class ReclamationSerializer(serializers.ModelSerializer):
    messages = MessageReclamationSerializer(many=True, read_only=True)
    photos = PhotoReclamationSerializer(many=True, read_only=True)
    client_nom = serializers.CharField(source='colis.client.nom', read_only=True)
    colis_code_suivi = serializers.CharField(source='colis.code_suivi', read_only=True)

    class Meta:
        model = Reclamation
        fields = '__all__'
        read_only_fields = ['client', 'statut', 'agent_assigne', 'cree_le', 'modifie_le']

    def validate_colis(self, colis):
        """Un client ne peut ouvrir une réclamation que sur SES colis (sinon il pouvait lire le nom du
        propriétaire et le code de suivi de n'importe quel colis en changeant l'identifiant)."""
        user = self.context['request'].user
        if hasattr(user, 'client') and colis.client_id != user.client.pk:
            raise serializers.ValidationError("Ce colis ne vous appartient pas.")
        return colis


class ReclamationStaffSerializer(ReclamationSerializer):
    """Mise à jour par le personnel : seuls le statut et l'assignation sont modifiables."""

    class Meta(ReclamationSerializer.Meta):
        read_only_fields = ['client', 'colis', 'type', 'description', 'photo_url', 'cree_le', 'modifie_le']
