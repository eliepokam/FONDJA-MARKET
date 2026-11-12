import phonenumbers
from phonenumbers import carrier, number_type, PhoneNumberType
from rest_framework import serializers

from .models import CompteBancaire, Paiement

# Un paiement échoué ou remboursé ne "bloque" plus le colis : le client peut repayer.
STATUTS_LIBERANT_LE_COLIS = (Paiement.Statut.ECHOUE, Paiement.Statut.REMBOURSE)

# Mot-clé attendu dans le nom d'opérateur renvoyé par la lib phonenumbers (ex. "MTN Cameroon", "Orange").
OPERATEUR_PAR_METHODE = {
    Paiement.Methode.MTN_MOMO: 'MTN',
    Paiement.Methode.ORANGE_MONEY: 'ORANGE',
}


def etape_virement(p):
    """preuve_a_fournir / en_verification / valide / refuse (ou « echoue » sans motif) ; None si ce n'est pas un virement."""
    if p.methode != Paiement.Methode.VIREMENT:
        return None
    if p.statut == Paiement.Statut.PAYE:
        return 'valide'
    if p.statut in (Paiement.Statut.ECHOUE, Paiement.Statut.REMBOURSE):
        return 'refuse' if p.motif_refus else 'echoue'
    return 'en_verification' if p.preuve else 'preuve_a_fournir'


def url_preuve(p, request=None):
    if not p.preuve:
        return None
    chemin = f"/api/paiements/{p.pk}/preuve-fichier/"
    return request.build_absolute_uri(chemin) if request else chemin


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

    # Étape d'un virement vue du client : preuve à fournir → en vérification → validé / refusé (None pour le Mobile Money)
    etape_virement = serializers.SerializerMethodField()
    # La preuve n'a PAS d'adresse publique : on donne l'adresse de l'API qui la sert après contrôle des droits.
    preuve_url = serializers.SerializerMethodField()

    class Meta:
        model = Paiement
        fields = ['id', 'colis', 'client', 'montant', 'devise', 'methode', 'moment', 'statut', 'telephone_paiement', 'reference_prestataire',
                  'paye_le', 'cree_le', 'modifie_le', 'preuve_url', 'preuve_envoyee_le', 'reference_virement', 'motif_refus', 'etape_virement']
        # montant : JAMAIS saisi par le client, toujours déduit du coût final du colis (voir perform_create).
        read_only_fields = ['client', 'montant', 'statut', 'reference_prestataire', 'paye_le', 'cree_le', 'modifie_le',
                            'preuve_envoyee_le', 'reference_virement', 'motif_refus']

    def get_etape_virement(self, obj):
        return etape_virement(obj)

    def get_preuve_url(self, obj):
        return url_preuve(obj, self.context.get('request'))

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
        methode, moment = attrs.get('methode'), attrs.get('moment')
        # Le paiement à la livraison n'existe plus (il ne reste que dans l'historique).
        if methode == Paiement.Methode.ESPECES or moment == Paiement.Moment.A_LA_LIVRAISON:
            raise serializers.ValidationError({'methode': "Le paiement à la livraison n'est plus proposé. "
                                                          "Choisissez le Mobile Money ou le virement bancaire."})
        if methode == Paiement.Methode.VIREMENT:
            if moment != Paiement.Moment.HORS_LIGNE:
                raise serializers.ValidationError({'moment': "Un virement bancaire se règle hors ligne."})
            if not CompteBancaire.objects.filter(actif=True).exists():
                raise serializers.ValidationError({'methode': "Le virement bancaire n'est pas encore disponible : "
                                                              "aucun compte bancaire n'est enregistré. Choisissez le Mobile Money."})
        elif moment != Paiement.Moment.EN_LIGNE:
            raise serializers.ValidationError({'moment': "Le Mobile Money se règle en ligne."})

        if moment == Paiement.Moment.EN_LIGNE:
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
            attendu = OPERATEUR_PAR_METHODE.get(methode)
            operateur = (carrier.name_for_number(numero, 'fr') or '').upper()
            if attendu and operateur and attendu not in operateur:
                raise serializers.ValidationError({
                    'telephone_paiement': f"Ce numéro est un numéro {carrier.name_for_number(numero, 'fr')} : "
                                          f"il ne correspond pas au moyen de paiement choisi."
                })
            attrs['telephone_paiement'] = phonenumbers.format_number(numero, phonenumbers.PhoneNumberFormat.E164)
        else:
            # Hors ligne (virement) : aucun numéro Mobile Money à stocker.
            attrs['telephone_paiement'] = None
        return attrs


class VirementPersonnelSerializer(serializers.ModelSerializer):
    """Un virement tel que le PERSONNEL (agent ou administrateur) le vérifie : montant, client, colis, preuve."""
    code_suivi = serializers.CharField(source='colis.code_suivi', read_only=True)
    colis_id = serializers.IntegerField(source='colis.pk', read_only=True)
    client_nom = serializers.CharField(source='client.nom', read_only=True)
    verifie_par_nom = serializers.CharField(source='verifie_par.nom', read_only=True, default=None)
    preuve_url = serializers.SerializerMethodField()
    etape = serializers.SerializerMethodField()

    class Meta:
        model = Paiement
        fields = ['id', 'colis_id', 'code_suivi', 'client_nom', 'montant', 'devise', 'statut', 'etape', 'reference_virement', 'preuve_url',
                  'preuve_envoyee_le', 'cree_le', 'verifie_par_nom', 'verifie_le', 'motif_refus', 'paye_le']

    def get_preuve_url(self, obj):
        return url_preuve(obj, self.context.get('request'))

    def get_etape(self, obj):
        return etape_virement(obj)


class CompteBancaireSerializer(serializers.ModelSerializer):
    class Meta:
        model = CompteBancaire
        fields = ['id', 'banque', 'titulaire', 'numero_compte', 'iban', 'code_swift', 'agence', 'instructions', 'actif', 'ordre', 'modifie_le']
        read_only_fields = ['id', 'modifie_le']

    def validate(self, attrs):
        for champ in ('banque', 'titulaire', 'numero_compte'):
            if champ in attrs and not str(attrs[champ]).strip():
                raise serializers.ValidationError({champ: "Ce champ est obligatoire."})
        return attrs
