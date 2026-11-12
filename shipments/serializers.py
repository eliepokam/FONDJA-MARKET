from decimal import Decimal
from rest_framework import serializers
from .models import AdresseTransit, Colis, Tarif
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
                             'cout_estime', 'cout_final', 'cree_le', 'modifie_le',
                             'lot', 'numero_besc', 'date_livraison_estimee', 'photo_declaree_url']   # réservés au personnel

class ColisAgentSerializer(serializers.ModelSerializer):
    class Meta:
        model = Colis
        fields = '__all__'
        read_only_fields = ['client', 'code_suivi', 'statut', 'cout_estime', 'cout_final', 'cree_le', 'modifie_le']

    CHAMPS_QUI_PILOTENT_LE_COUT = ('poids_kg', 'volume_m3', 'mode_transport')

    def validate(self, attrs):
        # Conteneurs : on ne charge pas un conteneur clôturé, et on ne sort pas un colis d'un conteneur clôturé.
        if 'lot' in attrs and self.instance is not None:
            nouveau, actuel = attrs['lot'], self.instance.lot
            if nouveau != actuel:
                if actuel is not None and actuel.statut == LotDedouanement.Statut.CLOTURE:
                    raise serializers.ValidationError({'lot': f"Ce colis est dans le conteneur clôturé {actuel.numero_besc} : il ne peut plus en sortir."})
                if nouveau is not None and nouveau.statut == LotDedouanement.Statut.CLOTURE:
                    raise serializers.ValidationError({'lot': f"Le conteneur {nouveau.numero_besc} est clôturé : plus aucun colis ne peut y être ajouté."})
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
    """Conteneur (lot d'embarquement) avec son remplissage. Le statut ne change que par les actions « clôturer » / « rouvrir »."""
    colis_count = serializers.SerializerMethodField()
    colis_livres = serializers.SerializerMethodField()
    colis_sans_volume = serializers.SerializerMethodField()
    volume_m3 = serializers.SerializerMethodField()
    pourcentage = serializers.SerializerMethodField()
    surcharge = serializers.SerializerMethodField()
    pret_a_cloturer = serializers.SerializerMethodField()
    type_conteneur_libelle = serializers.CharField(source='get_type_conteneur_display', read_only=True)

    class Meta:
        model = LotDedouanement
        fields = ['id', 'numero_besc', 'type_conteneur', 'type_conteneur_libelle', 'capacite_m3', 'statut', 'cree_le', 'cloture_le',
                  'colis_count', 'colis_livres', 'colis_sans_volume', 'volume_m3', 'pourcentage', 'surcharge', 'pret_a_cloturer']
        read_only_fields = ['statut', 'cloture_le', 'cree_le']
        extra_kwargs = {'capacite_m3': {'required': False}}

    # Valeurs calculées en UNE requête par la vue (annotations) ; le repli garde le serializer utilisable ailleurs.
    def get_colis_count(self, obj):
        return obj.nb_colis if hasattr(obj, 'nb_colis') else obj.colis.count()

    def get_colis_livres(self, obj):
        return obj.nb_livres if hasattr(obj, 'nb_livres') else obj.colis.filter(statut=Colis.Statut.LIVRE).count()

    def get_colis_sans_volume(self, obj):
        """Colis sans volume (ni déclaré, ni mesuré) : ils comptent pour 0 m³, donc le pourcentage est sous-estimé."""
        if hasattr(obj, 'nb_sans_volume'):
            return obj.nb_sans_volume
        return obj.colis.filter(volume_m3__isnull=True, volume_declare_m3__isnull=True).count()

    def _volume(self, obj):
        from .conteneurs import volume_du_lot
        return Decimal(obj.volume_total or 0) if hasattr(obj, 'volume_total') else volume_du_lot(obj)

    def get_volume_m3(self, obj):
        return float(self._volume(obj))

    def get_pourcentage(self, obj):
        from .conteneurs import pourcentage
        return float(pourcentage(self._volume(obj), obj.capacite_m3))

    def get_surcharge(self, obj):
        return self.get_pourcentage(obj) > 100

    def get_pret_a_cloturer(self, obj):
        """Tous les colis du conteneur sont livrés (et il y en a) : on peut le clôturer sans réserve."""
        total = self.get_colis_count(obj)
        return obj.statut == LotDedouanement.Statut.OUVERT and total > 0 and self.get_colis_livres(obj) == total

    def validate_numero_besc(self, value):
        # Deux lots portant le même numéro BESC se confondent en douane : refus, sans tenir compte de la casse ni des espaces.
        numero = value.strip()
        if not numero:
            raise serializers.ValidationError("Le numéro BESC est obligatoire.")
        doublons = LotDedouanement.objects.filter(numero_besc__iexact=numero)
        if self.instance is not None:
            doublons = doublons.exclude(pk=self.instance.pk)
        if doublons.exists():
            raise serializers.ValidationError("Un lot avec ce numéro BESC existe déjà.")
        return numero

    def validate(self, attrs):
        """Type standard → capacité utile standard (modifiable) ; « autre » → capacité obligatoire."""
        standard = LotDedouanement.CAPACITES_STANDARD
        type_ = attrs.get('type_conteneur', getattr(self.instance, 'type_conteneur', LotDedouanement.TypeConteneur.VINGT))
        if attrs.get('capacite_m3') is None:
            if self.instance is None or 'type_conteneur' in attrs:
                if type_ in standard:
                    attrs['capacite_m3'] = standard[type_]
                elif self.instance is None:
                    raise serializers.ValidationError({'capacite_m3': "Indiquez la capacité utile (m³) de ce conteneur."})
        return attrs


class TarifSerializer(serializers.ModelSerializer):
    class Meta:
        model = Tarif
        fields = '__all__'
        read_only_fields = ['administrateur', 'cree_le', 'modifie_le']

    def validate(self, data):
        def valeur(champ):
            return data[champ] if champ in data else getattr(self.instance, champ, None)

        delai_min, delai_max = valeur('delai_min_jours'), valeur('delai_max_jours')
        if delai_min is not None and delai_max is not None and delai_min > delai_max:
            raise serializers.ValidationError("Le délai minimum ne peut pas dépasser le délai maximum.")

        # Le prix doit correspondre au mode : au m³ pour le maritime, au kg pour l'aérien. Sans ce contrôle un tarif
        # sans prix pouvait être créé, et le coût des colis ne se calculait alors plus (« tarif manquant »).
        mode = valeur('mode_transport')
        prix_kg, prix_m3 = valeur('prix_par_kg'), valeur('prix_par_m3')
        if mode == 'maritime':
            if not prix_m3 or prix_m3 <= 0:
                raise serializers.ValidationError({'prix_par_m3': "Un tarif maritime doit avoir un prix au m³ supérieur à 0."})
        elif not prix_kg or prix_kg <= 0:
            raise serializers.ValidationError({'prix_par_kg': "Un tarif aérien doit avoir un prix au kg supérieur à 0."})
        return data
    
class PublicTarifSerializer(serializers.ModelSerializer):
    """Grille tarifaire exposée aux VISITEURS (landing page) : aucun identifiant interne, aucun administrateur, aucune date."""

    class Meta:
        model = Tarif
        fields = ['mode_transport', 'prix_par_kg', 'prix_par_m3', 'delai_min_jours', 'delai_max_jours', 'devise']
        read_only_fields = fields


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


class AdresseTransitSerializer(serializers.ModelSerializer):
    """Adresse de transit telle que le client la voit : champs détaillés + texte complet prêt à copier."""
    texte_a_copier = serializers.CharField(read_only=True)

    class Meta:
        model = AdresseTransit
        fields = ['id', 'mode', 'titre', 'adresse', 'destinataire', 'telephone', 'marquage', 'instructions', 'actif', 'texte_a_copier', 'modifie_le']
        read_only_fields = ['id', 'mode', 'texte_a_copier', 'modifie_le']

    def validate_adresse(self, value):
        if not value.strip():
            raise serializers.ValidationError("L'adresse ne peut pas être vide.")
        return value.strip()
