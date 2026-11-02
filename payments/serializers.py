import phonenumbers
from phonenumbers import carrier, number_type, PhoneNumberType
from rest_framework import serializers

from .models import Paiement

# Un paiement échoué ou remboursé ne "bloque" plus le colis : le client peut repayer.
STATUTS_LIBERANT_LE_COLIS = (Paiement.Statut.ECHOUE, Paiement.Statut.REMBOURSE)

# Mot-clé attendu dans le nom d'opérateur renvoyé par la lib phonenumbers (ex. "MTN Cameroon", "Orange").
OPERATEUR_PAR_METHODE = {
    Paiement.Methode.MTN_MOMO: 'MTN',
    Paiement.Methode.ORANGE_MONEY: 'ORANGE',
}


def normaliser_numero_cameroun(saisie):
    """
    Accepte "677123456", "+237 677 12 34 56"... et renvoie un PhoneNumber valide (mobile camerounais).
    Lève ValidationError sinon.
    """
    try:
        numero = phonenumbers.parse(str(saisie).strip(), 'CM')
    except phonenumbers.NumberParseException:
        raise serializers.ValidationError("Numéro de téléphone invalide.")
    if not phonenumbers.is_valid_number(numero):
        raise serializers.ValidationError("Numéro de téléphone invalide.")
    if numero.country_code != 237:
        raise serializers.ValidationError("Seuls les numéros camerounais (+237) sont acceptés pour le Mobile Money.")
    if number_type(numero) != PhoneNumberType.MOBILE:
        raise serializers.ValidationError("Ce numéro n'est pas un numéro mobile.")
    return numero


class PaiementSerializer(serializers.ModelSerializer):
    # Déclaré explicitement pour accepter aussi la saisie locale (677123456) et la normaliser en E.164.
    telephone_paiement = serializers.CharField(required=False, allow_null=True, allow_blank=True)

    class Meta:
        model = Paiement
        fields = '__all__'
        # montant : JAMAIS saisi par le client, toujours déduit du coût final du colis (voir perform_create).
        read_only_fields = ['client', 'montant', 'statut', 'reference_prestataire', 'paye_le',
                            'cree_le', 'modifie_le']

    def validate_colis(self, colis):
        user = self.context['request'].user
        if hasattr(user, 'client') and colis.client_id != user.client.pk:
            raise serializers.ValidationError("Ce colis ne vous appartient pas.")
        if colis.cout_final is None or colis.cout_final <= 0:
            raise serializers.ValidationError(
                "Ce colis n'a pas encore été pesé : le montant à payer n'est pas encore disponible."
            )
        if colis.paiements.exclude(statut__in=STATUTS_LIBERANT_LE_COLIS).exists():
            raise serializers.ValidationError("Un paiement est déjà en cours ou effectué pour ce colis.")
        return colis

    def validate(self, attrs):
        if attrs.get('moment') == Paiement.Moment.EN_LIGNE:
            saisie = attrs.get('telephone_paiement')
            if not saisie:
                raise serializers.ValidationError(
                    {'telephone_paiement': "Indiquez le numéro Mobile Money à débiter."}
                )
            try:
                numero = normaliser_numero_cameroun(saisie)
            except serializers.ValidationError as exc:
                raise serializers.ValidationError({'telephone_paiement': exc.detail})

            # Cohérence opérateur / moyen de paiement : on ne bloque que si l'opérateur est
            # identifié ET différent (une donnée d'opérateur absente ne doit jamais bloquer un client).
            attendu = OPERATEUR_PAR_METHODE.get(attrs.get('methode'))
            operateur = (carrier.name_for_number(numero, 'fr') or '').upper()
            if attendu and operateur and attendu not in operateur:
                raise serializers.ValidationError({
                    'telephone_paiement': f"Ce numéro est un numéro {carrier.name_for_number(numero, 'fr')} : "
                                          f"il ne correspond pas au moyen de paiement choisi."
                })
            attrs['telephone_paiement'] = phonenumbers.format_number(numero, phonenumbers.PhoneNumberFormat.E164)
        else:
            # Hors ligne (espèces / virement) : aucun numéro Mobile Money à stocker.
            attrs['telephone_paiement'] = None
        return attrs
