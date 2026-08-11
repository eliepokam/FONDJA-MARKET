from rest_framework import serializers
from .models import Colis

class ColisSerializer(serializers.ModelSerializer):
    class Meta:
        model = Colis
        fields = '__all__'
        read_only_fields = ['client', 'code_suivi', 'statut', 'cree_le', 'modifie_le']