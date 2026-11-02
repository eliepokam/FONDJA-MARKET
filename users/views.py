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

from rest_framework_simplejwt.views import TokenObtainPairView
from .models import Client, Utilisateur, CodeOtp, Agent, Administrateur
from .serializers import RegisterSerializer, StaffAccountSerializer, StaffSelfRegisterSerializer, VerifyOtpSerializer, UtilisateurSerializer, ClientSerializer, AgentSerializer, AdministrateurSerializer, ClientListSerializer, ChangePasswordSerializer, ForgotPasswordRequestSerializer, ResetPasswordSerializer
from .models import Utilisateur, CodeOtp, Administrateur, Agent, Client
logger = logging.getLogger('django')
from .permissions import IsAdministrateur
from django.shortcuts import get_object_or_404
from django.utils import timezone
from django.db.models.deletion import RestrictedError
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework import status
from notifications.services import notify
from notifications.models import Notification
from rest_framework_simplejwt.token_blacklist.models import OutstandingToken, BlacklistedToken
from rest_framework_simplejwt.views import TokenObtainPairView, TokenRefreshView
from django.conf import settings
from rest_framework_simplejwt.tokens import RefreshToken
from rest_framework_simplejwt.exceptions import InvalidToken, TokenError
from rest_framework.exceptions import ValidationError as DRFValidationError
from rest_framework.exceptions import Throttled
from core.services import log_action
from core.models import JournalActivite
from .tasks import envoyer_otp_task

COOKIE_KWARGS = dict(httponly=True, secure=not settings.DEBUG, samesite="Lax")
ACCESS_COOKIE = dict(COOKIE_KWARGS, max_age=int(settings.SIMPLE_JWT['ACCESS_TOKEN_LIFETIME'].total_seconds()))
REFRESH_COOKIE = dict(COOKIE_KWARGS, max_age=int(settings.SIMPLE_JWT['REFRESH_TOKEN_LIFETIME'].total_seconds()))
MAX_OTP_PAR_HEURE = 5

MAX_TENTATIVES_CONNEXION = 5
DUREE_BLOCAGE_MINUTES = 15

def blacklist_all_tokens(user):
    for token in OutstandingToken.objects.filter(user=user):
        BlacklistedToken.objects.get_or_create(token=token)

def check_otp_cooldown(utilisateur, cooldown_seconds=60):
    il_y_a_une_heure = timezone.now() - timedelta(hours=1)
    if CodeOtp.objects.filter(utilisateur=utilisateur, cree_le__gte=il_y_a_une_heure).count() >= MAX_OTP_PAR_HEURE:
        raise Throttled(detail="Trop de codes demandés. Réessaie dans une heure.")
    dernier_otp = CodeOtp.objects.filter(utilisateur=utilisateur).order_by('-cree_le').first()
    if dernier_otp and (timezone.now() - dernier_otp.cree_le).total_seconds() < cooldown_seconds:
        raise Throttled(detail="Un code vient déjà d'être envoyé. Réessaie dans quelques instants.")      

class CookieTokenObtainPairView(TokenObtainPairView):
    def post(self, request, *args, **kwargs):
        telephone = request.data.get('telephone')
        portail = request.data.get('portail', 'client')  # 'client' ou 'staff'
        utilisateur = Utilisateur.objects.filter(telephone=telephone).first()

        if utilisateur and utilisateur.bloque_jusqu_a and utilisateur.bloque_jusqu_a > timezone.now():
            minutes_restantes = int((utilisateur.bloque_jusqu_a - timezone.now()).total_seconds() / 60) + 1
            raise Throttled(detail=f"Trop de tentatives. Réessaie dans {minutes_restantes} min.")

        if utilisateur and utilisateur.statut == Utilisateur.Statut.DESACTIVE:
            return Response(
                {'detail': "Ce compte a été suspendu. Contacte le support Fondja Market pour plus d'informations."},
                status=status.HTTP_403_FORBIDDEN,
            )

        response = super().post(request, *args, **kwargs)

        if utilisateur:
            if response.status_code == 200:
                utilisateur.tentatives_connexion_echouees = 0
                utilisateur.bloque_jusqu_a = None
                utilisateur.save(update_fields=['tentatives_connexion_echouees', 'bloque_jusqu_a'])
            else:
                utilisateur.tentatives_connexion_echouees += 1
                if utilisateur.tentatives_connexion_echouees >= MAX_TENTATIVES_CONNEXION:
                    utilisateur.bloque_jusqu_a = timezone.now() + timedelta(minutes=DUREE_BLOCAGE_MINUTES)
                utilisateur.save(update_fields=['tentatives_connexion_echouees', 'bloque_jusqu_a'])

        if response.status_code != 200:
            return response

        # Le mot de passe est correct — mais est-on sur le bon portail pour ce rôle ?
        est_staff = hasattr(utilisateur, 'agent') or hasattr(utilisateur, 'administrateur')
        if portail == 'staff' and not est_staff:
            self._invalider_tokens(response)
            return Response({'detail': "Ce portail est réservé au personnel Fondja Market."}, status=status.HTTP_403_FORBIDDEN)
        if portail == 'client' and est_staff:
            self._invalider_tokens(response)
            return Response({'detail': "Utilise le portail professionnel pour te connecter."}, status=status.HTTP_403_FORBIDDEN)
        if hasattr(utilisateur, 'client') and utilisateur.telephone_verifie_le is None:
            # Sans cette vérification, l'étape OTP de l'inscription se contournait en se connectant directement,
            # et n'importe qui pouvait ouvrir un compte avec le numéro d'un tiers.
            self._invalider_tokens(response)
            return Response(
                {'detail': "Vérifie ton numéro de téléphone avant de te connecter.", 'code': 'phone_not_verified'},
                status=status.HTTP_403_FORBIDDEN,
            )

        access = response.data.pop("access")
        refresh = response.data.pop("refresh")
        response.set_cookie("access_token", access, **ACCESS_COOKIE)
        response.set_cookie("refresh_token", refresh, **REFRESH_COOKIE)
        return response

    def _invalider_tokens(self, response):
        # Le token vient d'être émis par SimpleJWT mais ne doit jamais servir — on le blackliste tout de suite
        try:
            RefreshToken(response.data.get('refresh')).blacklist()
        except (TokenError, KeyError):
            pass

class CookieTokenRefreshView(TokenRefreshView):
    @staticmethod
    def _session_expiree():
        reponse = Response({'detail': "Session expirée, reconnecte-toi."}, status=status.HTTP_401_UNAUTHORIZED)
        reponse.delete_cookie("access_token")
        reponse.delete_cookie("refresh_token")
        return reponse

    def post(self, request, *args, **kwargs):
        data = request.data.copy()
        data["refresh"] = request.COOKIES.get("refresh_token")
        request._full_data = data
        try:
            response = super().post(request, *args, **kwargs)
        except (InvalidToken, TokenError, DRFValidationError):
            # Cookie absent, expiré ou révoqué : un 401 propre (le front redirige vers la connexion),
            # là où l'ancien code plantait en 500 en cherchant la clé "access" d'une réponse d'erreur.
            return self._session_expiree()
        if response.status_code != 200 or "access" not in response.data:
            return self._session_expiree()
        response.set_cookie("access_token", response.data.pop("access"), **ACCESS_COOKIE)
        return response

class RegisterView(generics.CreateAPIView):
    serializer_class = RegisterSerializer
    permission_classes = [AllowAny]

    def perform_create(self, serializer):
        client = serializer.save()
        code = f"{random.randint(0, 999999):06d}"
        canal = self.request.data.get('canal', CodeOtp.Canal.SMS)
        if canal not in CodeOtp.Canal.values:
            canal = CodeOtp.Canal.SMS
        CodeOtp.objects.create(
            utilisateur=client, code=code, canal=canal,
            expire_le=timezone.now() + timedelta(minutes=5),
        )
        envoyer_otp_task.delay(str(client.telephone), code, canal)

class ResendOtpView(APIView):
    permission_classes = [AllowAny]

    def post(self, request):
        telephone = request.data.get('telephone')
        canal = request.data.get('canal', CodeOtp.Canal.SMS)
        if canal not in CodeOtp.Canal.values:
            canal = CodeOtp.Canal.SMS

        reponse_generique = Response({'detail': "Si ce numéro attend une vérification, un nouveau code a été envoyé."})
        utilisateur = Utilisateur.objects.filter(telephone=telephone).first()
        # Seuls les clients encore non vérifiés utilisent ce flux : pour tout autre cas on ne révèle rien et on n'envoie rien.
        if utilisateur is None or not hasattr(utilisateur, 'client') or utilisateur.telephone_verifie_le is not None:
            return reponse_generique

        check_otp_cooldown(utilisateur)

        code = f"{random.randint(0, 999999):06d}"
        CodeOtp.objects.create(
            utilisateur=utilisateur, code=code, canal=canal,
            expire_le=timezone.now() + timedelta(minutes=5),
        )
        envoyer_otp_task.delay(str(utilisateur.telephone), code, canal)
        return reponse_generique

class LogoutView(APIView):
    def post(self, request):
        token = request.COOKIES.get("refresh_token")
        if token:
            try:
                RefreshToken(token).blacklist()
            except TokenError:
                pass
        response = Response({'detail': 'Déconnecté.'})
        response.delete_cookie("access_token")
        response.delete_cookie("refresh_token")
        return response

class VerifyOtpView(APIView):
    permission_classes = [AllowAny]
    MAX_TENTATIVES = 5

    def post(self, request):
        serializer = VerifyOtpSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        telephone = serializer.validated_data['telephone']
        code = serializer.validated_data['code']

        utilisateur = Utilisateur.objects.filter(telephone=telephone).first()
        # Cette étape ne sert qu'à confirmer un numéro à l'inscription. Ouverte à un compte déjà vérifié ou au
        # personnel, elle permettrait de se connecter avec un simple code SMS, sans mot de passe.
        if utilisateur is None or not hasattr(utilisateur, 'client') or utilisateur.telephone_verifie_le is not None:
            return Response({'detail': 'Code invalide ou expiré.'}, status=status.HTTP_400_BAD_REQUEST)

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
        response = Response({'detail': 'Téléphone vérifié.'})
        response.set_cookie("access_token", str(refresh.access_token), **ACCESS_COOKIE)
        response.set_cookie("refresh_token", str(refresh), **REFRESH_COOKIE)
        return response

class UserListView(generics.ListAPIView):
    permission_classes = [IsAdministrateur]

    def get_serializer_class(self):
        if self.request.query_params.get('role') == 'client':
            return ClientListSerializer
        return UtilisateurSerializer

    def get_queryset(self):
        role = self.request.query_params.get('role')
        if role == 'client':
            return Client.objects.all().order_by('-cree_le')
        if role == 'agent':
            return Agent.objects.all().order_by('-cree_le')
        if role == 'administrateur':
            return Administrateur.objects.all().order_by('-cree_le')
        return Utilisateur.objects.none()

class MeView(generics.RetrieveAPIView):
    def get_serializer_class(self):
        user = self.request.user
        if hasattr(user, 'client'):
            return ClientSerializer
        if hasattr(user, 'agent'):
            return AgentSerializer
        if hasattr(user, 'administrateur'):
            return AdministrateurSerializer
        return UtilisateurSerializer

    def get_object(self):
        user = self.request.user
        if hasattr(user, 'client'):
            return user.client
        if hasattr(user, 'agent'):
            return user.agent
        if hasattr(user, 'administrateur'):
            return user.administrateur
        return user

class CreateStaffAccountView(generics.CreateAPIView):
    serializer_class = StaffAccountSerializer
    permission_classes = [IsAdministrateur]

    def get_serializer_context(self):
        context = super().get_serializer_context()
        return context

class StaffSelfRegisterView(generics.CreateAPIView):
    serializer_class = StaffSelfRegisterSerializer
    permission_classes = [AllowAny]

    def perform_create(self, serializer):
        agent = serializer.save()
        for admin in Administrateur.objects.all():
            notify(admin, f"Nouvelle demande d'inscription agent : {agent.nom} ({agent.telephone}) — en attente d'activation.", Notification.Type.GENERAL)

class ToggleUserStatusView(APIView):
    permission_classes = [IsAdministrateur]

    def post(self, request, user_id):
        target = get_object_or_404(Utilisateur, pk=user_id)
        new_status = request.data.get('statut')
        if new_status not in (Utilisateur.Statut.ACTIF, Utilisateur.Statut.DESACTIVE):
            return Response({'detail': "Statut invalide."}, status=status.HTTP_400_BAD_REQUEST)

        ancien_statut = target.statut
        target.statut = new_status
        target.save(update_fields=['statut'])

        action = JournalActivite.Action.SUSPENSION_COMPTE if new_status == Utilisateur.Statut.DESACTIVE else JournalActivite.Action.REACTIVATION_COMPTE
        log_action(request.user.administrateur, action, f"{target.nom} ({target.telephone})", ancien_statut=ancien_statut, nouveau_statut=new_status)

        message = (
            "Votre compte a été réactivé."
            if new_status == Utilisateur.Statut.ACTIF
            else "Votre compte a été suspendu. Contactez le support pour plus d'informations."
        )
        notify(target, message, Notification.Type.GENERAL)

        return Response({'detail': 'Statut mis à jour.', 'statut': target.statut})


class DeleteUserView(APIView):
    permission_classes = [IsAdministrateur]

    def delete(self, request, user_id):
        target = get_object_or_404(Utilisateur, pk=user_id)
        description = f"{target.nom} ({target.telephone})"

        if hasattr(target, 'agent'):
            agent = target.agent
            agent.statut = Utilisateur.Statut.DESACTIVE
            agent.supprime_par = request.user.administrateur
            agent.supprime_le = timezone.now()
            agent.save(update_fields=['statut', 'supprime_par', 'supprime_le'])
            log_action(request.user.administrateur, JournalActivite.Action.SUPPRESSION_COMPTE, description, type='agent_desactive')
            return Response({'detail': 'Agent désactivé.'})

        try:
            target.delete()
        except RestrictedError:
            return Response(
                {'detail': "Ce compte a des colis ou des données associées — impossible à supprimer. Désactivez-le plutôt."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        log_action(request.user.administrateur, JournalActivite.Action.SUPPRESSION_COMPTE, description)
        return Response({'detail': 'Compte supprimé.'})

class ChangePasswordView(APIView):
    def post(self, request):
        serializer = ChangePasswordSerializer(data=request.data, context={'request': request})
        serializer.is_valid(raise_exception=True)
        request.user.set_password(serializer.validated_data['new_password'])
        request.user.save()
        blacklist_all_tokens(request.user)   # déconnecte tous les appareils...
        refresh = RefreshToken.for_user(request.user)   # ...sauf celui-ci, qui repart avec une session neuve
        response = Response({'detail': 'Mot de passe modifié avec succès.'})
        response.set_cookie("access_token", str(refresh.access_token), **ACCESS_COOKIE)
        response.set_cookie("refresh_token", str(refresh), **REFRESH_COOKIE)
        return response
    
class ForgotPasswordRequestView(APIView):
    permission_classes = [AllowAny]

    def post(self, request):
        serializer = ForgotPasswordRequestSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        telephone = serializer.validated_data['telephone']
        canal = request.data.get('canal', CodeOtp.Canal.SMS)
        if canal not in CodeOtp.Canal.values:
            canal = CodeOtp.Canal.SMS

        try:
            utilisateur = Utilisateur.objects.get(telephone=telephone)
        except Utilisateur.DoesNotExist:
            return Response({'detail': 'Si ce numéro existe, un code a été envoyé.'})

        check_otp_cooldown(utilisateur)

        code = f"{random.randint(0, 999999):06d}"
        CodeOtp.objects.create(
            utilisateur=utilisateur, code=code, canal=canal,
            expire_le=timezone.now() + timedelta(minutes=5),
        )
        envoyer_otp_task.delay(str(utilisateur.telephone), code, canal)   # ← vrai envoi, plus le logger.info simulé
        return Response({'detail': 'Si ce numéro existe, un code a été envoyé.'})


class ResetPasswordView(APIView):
    permission_classes = [AllowAny]
    MAX_TENTATIVES = 5

    def post(self, request):
        serializer = ResetPasswordSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        telephone = serializer.validated_data['telephone']
        code = serializer.validated_data['code']
        new_password = serializer.validated_data['new_password']

        try:
            utilisateur = Utilisateur.objects.get(telephone=telephone)
        except Utilisateur.DoesNotExist:
            return Response({'detail': 'Code invalide ou expiré.'}, status=status.HTTP_400_BAD_REQUEST)

        otp = CodeOtp.objects.filter(
            utilisateur=utilisateur, verifie_le__isnull=True
        ).order_by('-cree_le').first()

        if not otp or otp.expire_le < timezone.now():
            return Response({'detail': 'Code invalide ou expiré.'}, status=status.HTTP_400_BAD_REQUEST)
        if otp.tentatives >= self.MAX_TENTATIVES:
            return Response({'detail': 'Trop de tentatives. Redemandez un code.'}, status=status.HTTP_429_TOO_MANY_REQUESTS)
        if otp.code != code:
            otp.tentatives += 1
            otp.save()
            return Response({'detail': 'Code incorrect.'}, status=status.HTTP_400_BAD_REQUEST)

        otp.verifie_le = timezone.now()
        otp.save()
        utilisateur.set_password(new_password)
        utilisateur.tentatives_connexion_echouees = 0
        utilisateur.bloque_jusqu_a = None
        if hasattr(utilisateur, 'client') and utilisateur.telephone_verifie_le is None:
            utilisateur.telephone_verifie_le = timezone.now()   # le code reçu par SMS prouve que le numéro est le sien
        utilisateur.save()
        blacklist_all_tokens(utilisateur) 
        return Response({'detail': 'Mot de passe réinitialisé avec succès.'})