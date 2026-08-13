from rest_framework import serializers
from .models import Colis, Tarif
from .models import LotDedouanement

class ColisSerializer(serializers.ModelSerializer):
    class Meta:
        model = Colis
        fields = '__all__'
        read_only_fields = ['client', 'code_suivi', 'statut', 'cree_le', 'modifie_le']

class LotDedouanementSerializer(serializers.ModelSerializer):
    class Meta:
        model = LotDedouanement
        fields = '__all__'
        read_only_fields = ['administrateur', 'cree_le']

class TarifSerializer(serializers.ModelSerializer):
    class Meta:
        model = Tarif
        fields = '__all__'
        read_only_fields = ['administrateur', 'cree_le', 'modifie_le']