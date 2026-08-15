import random
import logging
from datetime import timedelta

from django.utils import timezone
from rest_framework import generics, status
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework.permissions import AllowAny
from rest_framework_simplejwt.tokens import RefreshToken
from rest_framework import generics
from rest_framework import serializers
from .models import Client, Utilisateur, CodeOtp
from .serializers import RegisterSerializer, VerifyOtpSerializer, UtilisateurSerializer
from .models import Utilisateur, CodeOtp
from .serializers import RegisterSerializer, VerifyOtpSerializer

logger = logging.getLogger('django')

class RegisterView(generics.CreateAPIView):
    serializer_class = RegisterSerializer
    permission_classes = [AllowAny]

    def perform_create(self, serializer):
        client = serializer.save()
        code = f"{random.randint(0, 999999):06d}"
        canal = self.request.data.get('canal', CodeOtp.Canal.SMS)
        CodeOtp.objects.create(
            utilisateur=client,   # Client hérite d'Utilisateur (MTI) — la FK accepte directement l'instance
            code=code,
            canal=canal,
            expire_le=timezone.now() + timedelta(minutes=5),
        )
        # Simulation — le vrai envoi SMS/WhatsApp viendra plus tard
        logger.info(f"[OTP SIMULÉ] {code} envoyé à {client.telephone} via {canal}")

class VerifyOtpView(APIView):
    permission_classes = [AllowAny]
    MAX_TENTATIVES = 5

    def post(self, request):
        serializer = VerifyOtpSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        telephone = serializer.validated_data['telephone']
        code = serializer.validated_data['code']

        try:
            utilisateur = Utilisateur.objects.get(telephone=telephone)
        except Utilisateur.DoesNotExist:
            return Response({'detail': 'Utilisateur introuvable.'}, status=status.HTTP_404_NOT_FOUND)

        otp = CodeOtp.objects.filter(
            utilisateur=utilisateur, verifie_le__isnull=True
        ).order_by('-cree_le').first()

        if not otp or otp.expire_le < timezone.now():
            return Response({'detail': 'Code invalide ou expiré.'}, status=status.HTTP_400_BAD_REQUEST)
        if otp.tentatives >= self.MAX_TENTATIVES:
            return Response(
                {'detail': 'Trop de tentatives. Demandez un nouveau code.'},
                status=status.HTTP_429_TOO_MANY_REQUESTS
            )

        if otp.code != code:
            otp.tentatives += 1
            otp.save()
            restantes = self.MAX_TENTATIVES - otp.tentatives
            return Response(
                {'detail': f'Code incorrect. {restantes} tentative(s) restante(s).'},
                status=status.HTTP_400_BAD_REQUEST
            )


        otp.verifie_le = timezone.now()
        otp.save()
        utilisateur.telephone_verifie_le = timezone.now()
        utilisateur.save()

        refresh = RefreshToken.for_user(utilisateur)
        return Response({'access': str(refresh.access_token), 'refresh': str(refresh)})


class MeView(generics.RetrieveAPIView):
    serializer_class = UtilisateurSerializer

    def get_object(self):
        return self.request.user