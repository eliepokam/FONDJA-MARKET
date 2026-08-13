from rest_framework import serializers
from .models import Paiement

class PaiementSerializer(serializers.ModelSerializer):
    class Meta:
        model = Paiement
        fields = '__all__'
        read_only_fields = ['client', 'statut', 'reference_prestataire', 'paye_le', 'cree_le', 'modifie_le']