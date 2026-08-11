from rest_framework import serializers
from shipments.models import Colis


class ChangerStatutSerializer(serializers.Serializer):
    statut = serializers.ChoiceField(choices=Colis.Statut.choices)
    note = serializers.CharField(required=False, allow_blank=True)