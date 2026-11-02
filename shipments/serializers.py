from rest_framework import serializers
from .models import Colis, Tarif
from .models import LotDedouanement
from .models import Tarif
from .models import Incident
from .pricing import CoutIndisponible, calculer_cout
from notifications.models import Notification
from notifications.services import notify


class ColisSerializer(serializers.ModelSerializer):
    client_nom = serializers.CharField(source='client.nom', read_only=True)
    client_telephone = serializers.CharField(source='client.telephone', read_only=True)
    lot_numero_besc = serializers.CharField(source='lot.numero_besc', read_only=True, allow_null=True)
    class Meta:
        model = Colis
        fields = '__all__'
        read_only_fields = ['client', 'code_suivi', 'statut', 'poids_kg', 'volume_m3',
                             'cout_estime', 'cout_final', 'cree_le', 'modifie_le']

class ColisAgentSerializer(serializers.ModelSerializer):
    class Meta:
        model = Colis
        fields = '__all__'
        read_only_fields = ['client', 'code_suivi', 'statut', 'cout_estime', 'cout_final', 'cree_le', 'modifie_le']

    CHAMPS_QUI_PILOTENT_LE_COUT = ('poids_kg', 'volume_m3', 'mode_transport')

    def validate(self, attrs):
        # Une fois qu'un paiement est en cours ou effectué, le coût ne doit plus bouger.
        if self.instance is not None:
            change = any(
                champ in attrs and attrs[champ] != getattr(self.instance, champ)
                for champ in self.CHAMPS_QUI_PILOTENT_LE_COUT
            )
            if change and self.instance.paiements.exclude(statut__in=('echoue', 'rembourse')).exists():
                raise serializers.ValidationError(
                    "Un paiement est déjà en cours ou effectué pour ce colis : le coût ne peut plus être modifié."
                )
        return attrs

    def update(self, instance, validated_data):
        instance = super().update(instance, validated_data)

        if any(champ in validated_data for champ in self.CHAMPS_QUI_PILOTENT_LE_COUT):
            try:
                nouveau_cout = calculer_cout(instance)
            except CoutIndisponible:
                # Mesures enregistrées mais coût incalculable (tarif manquant...) : on efface un éventuel
                # ancien coût devenu faux plutôt que de laisser le client payer un montant périmé.
                nouveau_cout = None

            if nouveau_cout != instance.cout_final:
                instance.cout_final = nouveau_cout
                instance.save(update_fields=['cout_final', 'modifie_le'])
                if nouveau_cout is not None:
                    montant = f"{int(nouveau_cout):,}".replace(',', ' ')
                    notify(
                        instance.client,
                        f"Votre colis {instance.code_suivi} a été pesé : montant à payer {montant} XAF.",
                        Notification.Type.PAIEMENT,
                        colis=instance,
                    )
        return instance

class LotDedouanementSerializer(serializers.ModelSerializer):
    colis_count = serializers.SerializerMethodField()

    class Meta:
        model = LotDedouanement
        fields = '__all__'
        read_only_fields = ['administrateur', 'cree_le']

    def get_colis_count(self, obj):
        # `nb_colis` est calculé en une seule requête par la vue ; le repli garde le serializer utilisable ailleurs.
        return obj.nb_colis if hasattr(obj, 'nb_colis') else obj.colis.count()
    
class TarifSerializer(serializers.ModelSerializer):
    class Meta:
        model = Tarif
        fields = '__all__'
        read_only_fields = ['administrateur', 'cree_le', 'modifie_le']

    def validate(self, data):
        delai_min = data.get('delai_min_jours', getattr(self.instance, 'delai_min_jours', None))
        delai_max = data.get('delai_max_jours', getattr(self.instance, 'delai_max_jours', None))
        if delai_min is not None and delai_max is not None and delai_min > delai_max:
            raise serializers.ValidationError("Le délai minimum ne peut pas dépasser le délai maximum.")
        return data
    
class IncidentSerializer(serializers.ModelSerializer):
    colis_count = serializers.SerializerMethodField()
    colis_code_suivi = serializers.CharField(source='colis.code_suivi', read_only=True, allow_null=True)
    lot_numero_besc = serializers.CharField(source='lot.numero_besc', read_only=True, allow_null=True)

    class Meta:
        model = Incident
        fields = '__all__'
        read_only_fields = ['declare_par', 'resolu_le', 'cree_le']

    def validate(self, attrs):
        # La base impose « soit un colis, soit un lot, jamais les deux ni aucun » (contrainte CHECK) : on le dit
        # clairement ici au lieu de laisser la base répondre par une erreur 500.
        colis = attrs.get('colis', getattr(self.instance, 'colis', None))
        lot = attrs.get('lot', getattr(self.instance, 'lot', None))
        if bool(colis) == bool(lot):
            raise serializers.ValidationError("Un imprévu concerne soit un colis, soit un lot de dédouanement (pas les deux, pas aucun).")
        return attrs

    def get_colis_count(self, obj):
        if obj.colis_id:
            return 1
        if obj.lot_id:
            return obj.lot.colis.count()
        return 0