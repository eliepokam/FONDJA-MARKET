from rest_framework import serializers
from .models import JournalActivite

class JournalActiviteSerializer(serializers.ModelSerializer):
    auteur_nom = serializers.CharField(source='auteur.nom', read_only=True, default='Système')

    class Meta:
        model = JournalActivite
        fields = '__all__'