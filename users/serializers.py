from rest_framework import serializers
from .models import Client, Utilisateur
from .models import Client


class RegisterSerializer(serializers.ModelSerializer):
    password = serializers.CharField(write_only=True)

    class Meta:
        model = Client
        fields = ['nom', 'telephone', 'email', 'password', 'code_client']
        extra_kwargs = {'code_client': {'required': False}}

    def create(self, validated_data):
        password = validated_data.pop('password')
        if not validated_data.get('code_client'):
            validated_data['code_client'] = f"CL-{Client.objects.count() + 1:05d}"
        client = Client(**validated_data)
        client.set_password(password)   # hashing géré par AbstractBaseUser, pas besoin de make_password
        client.save()
        return client


class VerifyOtpSerializer(serializers.Serializer):
    telephone = serializers.CharField()
    code = serializers.CharField()

class UtilisateurSerializer(serializers.ModelSerializer):
    class Meta:
        model = Utilisateur
        fields = ['id', 'nom', 'telephone', 'email', 'statut', 'cree_le']
        read_only_fields = fields