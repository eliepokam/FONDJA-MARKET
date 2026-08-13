from rest_framework import serializers
from .models import Reclamation, MessageReclamation


class MessageReclamationSerializer(serializers.ModelSerializer):
    class Meta:
        model = MessageReclamation
        fields = '__all__'
        read_only_fields = ['reclamation', 'expediteur', 'cree_le']

class ReclamationSerializer(serializers.ModelSerializer):
    messages = MessageReclamationSerializer(many=True, read_only=True)

    class Meta:
        model = Reclamation
        fields = '__all__'
        read_only_fields = ['client', 'statut', 'agent_assigne', 'cree_le', 'modifie_le']