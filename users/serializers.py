from django.contrib.auth.password_validation import validate_password
from django.db import transaction
from rest_framework import serializers
from .models import Utilisateur, Client, Agent, Administrateur
from django.db import IntegrityError
from django.db import IntegrityError
from core.services import log_action
from core.models import JournalActivite
from phonenumber_field.serializerfields import PhoneNumberField as PhoneNumberSerializerField

class RegisterSerializer(serializers.ModelSerializer):
    password = serializers.CharField(write_only=True, min_length=8)

    class Meta:
        model = Client
        fields = ['nom', 'telephone', 'email', 'password']
        extra_kwargs = {'email': {'required': False, 'allow_blank': True}}

    def validate_password(self, value):
        validate_password(value)   # refuse les mots de passe triviaux (12345678, password...)
        return value

    def create(self, validated_data):
        password = validated_data.pop('password')
        client = Client(**validated_data)
        client.set_password(password)
        # Une seule transaction : le code client (déduit de l'identifiant) est posé avant que
        # quiconque d'autre ne puisse voir la ligne, donc deux inscriptions simultanées ne se heurtent
        # plus sur la valeur vide de la colonne unique `code_client`.
        with transaction.atomic():
            client.save()
            client.code_client = f"CL-{client.pk:05d}"
            client.save(update_fields=['code_client'])
        return client


class VerifyOtpSerializer(serializers.Serializer):
    telephone = serializers.CharField()
    code = serializers.CharField()

class UtilisateurSerializer(serializers.ModelSerializer):
    class Meta:
        model = Utilisateur
        fields = ['id', 'nom', 'telephone', 'email', 'statut', 'cree_le']
        read_only_fields = fields

class ClientSerializer(UtilisateurSerializer):
    role = serializers.SerializerMethodField()

    class Meta(UtilisateurSerializer.Meta):
        model = Client
        fields = UtilisateurSerializer.Meta.fields + ['code_client', 'role']

    def get_role(self, obj):
        return 'client'

class ClientListSerializer(serializers.ModelSerializer):
    colis_actifs_count = serializers.SerializerMethodField()

    class Meta:
        model = Client
        fields = ['id', 'nom', 'telephone', 'statut', 'cree_le', 'colis_actifs_count']

    def get_colis_actifs_count(self, obj):
        return obj.colis.exclude(statut='livre').count()
    
class AgentSerializer(UtilisateurSerializer):
    role = serializers.SerializerMethodField()
    statuts_autorises = serializers.SerializerMethodField()

    class Meta(UtilisateurSerializer.Meta):
        model = Agent
        fields = UtilisateurSerializer.Meta.fields + ['role', 'localisation', 'statuts_autorises']

    def get_role(self, obj):
        return 'agent'

    def get_statuts_autorises(self, obj):
        from shipments.status import statuts_autorises   # import local : évite une dépendance circulaire entre les apps
        return statuts_autorises(obj)


class AdministrateurSerializer(UtilisateurSerializer):
    role = serializers.SerializerMethodField()
    statuts_autorises = serializers.SerializerMethodField()

    class Meta(UtilisateurSerializer.Meta):
        model = Administrateur
        fields = UtilisateurSerializer.Meta.fields + ['role', 'statuts_autorises']

    def get_role(self, obj):
        return 'administrateur'

    def get_statuts_autorises(self, obj):
        from shipments.status import statuts_autorises
        return statuts_autorises(obj)

class StaffSelfRegisterSerializer(serializers.Serializer):
    nom = serializers.CharField()
    telephone = PhoneNumberSerializerField()
    localisation = serializers.ChoiceField(choices=["chine", "cameroun"])
    password = serializers.CharField(write_only=True, min_length=8)

    def create(self, validated_data):
        password = validated_data.pop('password')
        agent = Agent(**validated_data, cree_par=None, statut=Utilisateur.Statut.DESACTIVE)
        agent.set_password(password)
        try:
            agent.save()
        except IntegrityError:
            raise serializers.ValidationError({'telephone': "Ce numéro de téléphone est déjà utilisé."})
        return agent

    def to_representation(self, instance):
        return {"id": instance.id, "nom": instance.nom, "telephone": str(instance.telephone)}

class StaffAccountSerializer(serializers.Serializer):
    role = serializers.ChoiceField(choices=["agent", "administrateur"])
    nom = serializers.CharField()
    telephone = PhoneNumberSerializerField()   # ← remplace CharField, valide le vrai format
    localisation = serializers.ChoiceField(choices=["chine", "cameroun"], required=False)
    password = serializers.CharField(write_only=True)

    def validate(self, data):
        if data['role'] == 'agent' and not data.get('localisation'):
            raise serializers.ValidationError({'localisation': "L'affectation d'un agent est obligatoire."})
        return data

    def create(self, validated_data):
        role = validated_data.pop('role')
        password = validated_data.pop('password')
        localisation = validated_data.pop('localisation', None)
        model = Agent if role == "agent" else Administrateur
        auteur = self.context['request'].user.administrateur

        if role == "agent":
            validated_data['cree_par'] = auteur
            validated_data['localisation'] = localisation

        instance = model(**validated_data)
        instance.set_password(password)
        try:
            instance.save()
        except IntegrityError:
            raise serializers.ValidationError({'telephone': "Ce numéro de téléphone est déjà utilisé."})

        log_action(auteur, JournalActivite.Action.CREATION_STAFF, f"{instance.nom} ({instance.telephone})", role=role, localisation=localisation)
        return instance

    def to_representation(self, instance):
        return {"id": instance.id, "nom": instance.nom, "telephone": str(instance.telephone)}

class ChangePasswordSerializer(serializers.Serializer):
    current_password = serializers.CharField(write_only=True)
    new_password = serializers.CharField(write_only=True, min_length=8)

    def validate_current_password(self, value):
        user = self.context['request'].user
        if not user.check_password(value):
            raise serializers.ValidationError("Mot de passe actuel incorrect.")
        return value

    def validate_new_password(self, value):
        validate_password(value, user=self.context['request'].user)
        return value


class ForgotPasswordRequestSerializer(serializers.Serializer):
    telephone = serializers.CharField()


class ResetPasswordSerializer(serializers.Serializer):
    telephone = serializers.CharField()
    code = serializers.CharField()
    new_password = serializers.CharField(min_length=8)

    def validate_new_password(self, value):
        validate_password(value)
        return value