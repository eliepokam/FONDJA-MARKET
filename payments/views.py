import logging

from django.conf import settings
from django.db import transaction
from django.utils import timezone
from rest_framework import mixins, serializers, viewsets, permissions, status
from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework.views import APIView

from .models import Paiement
from .serializers import PaiementSerializer, STATUTS_LIBERANT_LE_COLIS
from .services import FreeMoPayError, FREEMOPAY_STATUS_MAP, get_gateway
from notifications.models import Notification
from notifications.services import notify
from shipments.models import Colis
from users.permissions import IsClient, IsAdministrateur

logger = logging.getLogger('django')


def appliquer_statut_freemopay(paiement):
    """
    Interroge FreeMoPay (appel authentifié par nos clés) et met le Paiement à jour.

    Partagé par le webhook ET par l'action « verifier » : si le callback n'arrive jamais
    (tunnel coupé, URL mal configurée...), le front peut quand même faire converger le statut.
    Retourne (detail, code_http).
    """
    reference = paiement.reference_prestataire
    try:
        data = get_gateway().get_status(reference)
    except FreeMoPayError as exc:
        logger.error(f"Échec vérification de statut FreeMoPay pour {reference} : {exc}")
        return str(exc), status.HTTP_502_BAD_GATEWAY

    statut_freemopay = data.get('status')
    if statut_freemopay not in FREEMOPAY_STATUS_MAP:
        return 'Statut FreeMoPay inconnu.', status.HTTP_400_BAD_REQUEST

    nouveau_statut = FREEMOPAY_STATUS_MAP[statut_freemopay]

    if paiement.statut == nouveau_statut:
        # FreeMoPay peut renvoyer plusieurs fois le même événement : on reste idempotent.
        return 'Déjà à jour.', status.HTTP_200_OK

    if paiement.statut in (Paiement.Statut.PAYE, Paiement.Statut.REMBOURSE):
        logger.warning(
            f"Webhook FreeMoPay ignoré : le paiement {paiement.pk} est déjà {paiement.statut}, "
            f"FreeMoPay annonce {nouveau_statut}."
        )
        return 'Paiement déjà finalisé, changement ignoré.', status.HTTP_200_OK

    paiement.statut = nouveau_statut
    if nouveau_statut == Paiement.Statut.PAYE:
        paiement.paye_le = timezone.now()
    paiement.save(update_fields=['statut', 'paye_le', 'modifie_le'])
    logger.info(f"Paiement {paiement.pk} mis à jour via FreeMoPay -> {nouveau_statut}")

    montant = f"{int(paiement.montant):,}".replace(',', ' ')
    code = paiement.colis.code_suivi
    if nouveau_statut == Paiement.Statut.PAYE:
        notify(paiement.client, f"Paiement de {montant} XAF confirmé pour le colis {code}.",
               Notification.Type.PAIEMENT, colis=paiement.colis)
    elif nouveau_statut == Paiement.Statut.ECHOUE:
        notify(paiement.client, f"Le paiement de {montant} XAF pour le colis {code} a échoué. Vous pouvez réessayer.",
               Notification.Type.PAIEMENT, colis=paiement.colis)
    return 'ok', status.HTTP_200_OK


class PaiementViewSet(mixins.CreateModelMixin, mixins.ListModelMixin,
                      mixins.RetrieveModelMixin, viewsets.GenericViewSet):
    serializer_class = PaiementSerializer
    filterset_fields = ['statut', 'methode', 'colis']

    def get_queryset(self):
        user = self.request.user
        if hasattr(user, 'client'):
            return Paiement.objects.filter(client=user.client)
        return Paiement.objects.all()  # administrateur/agent voient tout

    def perform_create(self, serializer):
        colis = serializer.validated_data['colis']
        with transaction.atomic():
            # Verrou sur le colis : deux requêtes simultanées ne peuvent pas créer deux paiements actifs.
            colis = Colis.objects.select_for_update().get(pk=colis.pk)
            if colis.paiements.exclude(statut__in=STATUTS_LIBERANT_LE_COLIS).exists():
                raise serializers.ValidationError(
                    {'colis': ["Un paiement est déjà en cours ou effectué pour ce colis."]}
                )
            # Le montant vient du serveur (coût final calculé à la pesée), jamais de la requête.
            serializer.save(client=self.request.user.client, montant=colis.cout_final)

    def get_permissions(self):
        if self.action in ('create', 'initier'):
            return [IsClient()]
        if self.action == 'confirmer':
            return [IsAdministrateur()]
        return [permissions.IsAuthenticated()]

    @action(detail=True, methods=['post'])
    def confirmer(self, request, pk=None):
        """Confirmation manuelle par un administrateur (ex. espèces à la livraison)."""
        paiement = self.get_object()
        paiement.statut = Paiement.Statut.PAYE
        paiement.reference_prestataire = paiement.reference_prestataire or f"MANUEL-{paiement.pk:06d}"
        paiement.paye_le = timezone.now()
        paiement.save()
        return Response({'detail': 'Paiement confirmé manuellement.', 'statut': paiement.statut})

    @action(detail=True, methods=['post'])
    def initier(self, request, pk=None):
        """
        Démarre le paiement en ligne via FreeMoPay pour ce Paiement.

        Contrairement à CamerPay, FreeMoPay ne renvoie pas d'URL de paiement à
        afficher : un push USSD est envoyé directement sur le téléphone du
        client, qui doit le valider lui-même. Le statut du Paiement N'EST PAS
        mis à jour ici : il reste 'en_attente' jusqu'à confirmation (voir
        FreeMoPayWebhookView).
        """
        paiement = self.get_object()

        if paiement.moment != Paiement.Moment.EN_LIGNE:
            return Response(
                {'detail': "Ce paiement n'est pas configuré pour un règlement en ligne."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        if paiement.statut != Paiement.Statut.EN_ATTENTE:
            return Response(
                {'detail': "Ce paiement a déjà été traité."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        if paiement.reference_prestataire:
            return Response(
                {'detail': "Une demande de paiement est déjà en cours pour ce paiement."},
                status=status.HTTP_409_CONFLICT,
            )
        if paiement.methode not in (Paiement.Methode.MTN_MOMO, Paiement.Methode.ORANGE_MONEY):
            return Response(
                {'detail': "Cette méthode de paiement n'est pas prise en charge par FreeMoPay."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        client_freemopay = get_gateway()
        try:
            data = client_freemopay.create_payment(
                amount=paiement.montant,
                # Numéro choisi par le client au moment de payer ; repli sur son numéro de compte
                # pour les anciens paiements créés avant l'ajout de ce champ.
                # Format attendu par FreeMoPay : indicatif complet SANS le "+" (ex. "237699123456").
                payer_phone=str(paiement.telephone_paiement or paiement.client.telephone).lstrip('+'),
                external_id=str(paiement.pk),
                description=f"Paiement Fondja Market — colis {paiement.colis.code_suivi}",
                callback_url=settings.FREEMOPAY_CALLBACK_URL,
            )
        except FreeMoPayError as exc:
            logger.error(f"Échec initiation FreeMoPay pour le paiement {paiement.pk} : {exc}")
            return Response({'detail': str(exc)}, status=status.HTTP_502_BAD_GATEWAY)

        # On garde une trace de la référence FreeMoPay : c'est CE champ que le
        # webhook (et get_status) utiliseront pour retrouver le Paiement.
        paiement.reference_prestataire = data.get('reference')
        paiement.save(update_fields=['reference_prestataire', 'modifie_le'])

        return Response({
            'detail': "Une demande de paiement a été envoyée sur le téléphone du client. "
                      "Validez-la pour confirmer.",
            'reference': data.get('reference'),
        })

    @action(detail=True, methods=['post'])
    def verifier(self, request, pk=None):
        """
        Resynchronise ce paiement avec FreeMoPay à la demande (le front l'appelle pendant l'attente).
        Rattrape un callback perdu : ne change rien si le paiement n'est pas en attente de confirmation.
        """
        paiement = self.get_object()
        if paiement.statut == Paiement.Statut.EN_ATTENTE and paiement.reference_prestataire:
            detail, code = appliquer_statut_freemopay(paiement)
            if code == status.HTTP_502_BAD_GATEWAY:
                return Response({'detail': detail}, status=code)
            paiement.refresh_from_db()
        return Response(self.get_serializer(paiement).data)


class FreeMoPayWebhookView(APIView):
    """
    Point d'entrée appelé par les SERVEURS de FreeMoPay (jamais par le
    navigateur du client) pour notifier tout changement de statut d'un
    paiement.

    ⚠️ FreeMoPay ne signe PAS ses callbacks (pas de HMAC comme CamerPay).
    Cette vue ne fait donc JAMAIS confiance au contenu du payload reçu pour
    le statut — elle l'utilise uniquement pour savoir QUELLE transaction
    vérifier, puis interroge FreeMoPay elle-même (authentifié) via
    get_status() pour connaître le vrai résultat. Un attaquant peut forger un
    POST vers cette URL, mais il ne peut pas forger la réponse de FreeMoPay.

    Reste volontairement public (pas d'IsAuthenticated) : FreeMoPay n'a pas de
    compte utilisateur chez nous.
    """
    authentication_classes = []
    permission_classes = [permissions.AllowAny]
    throttle_classes = []

    def post(self, request):
        reference = request.data.get('reference') if isinstance(request.data, dict) else None
        if not reference or not isinstance(reference, str):
            return Response({'detail': 'Payload invalide.'}, status=status.HTTP_400_BAD_REQUEST)

        paiement = Paiement.objects.filter(reference_prestataire=reference).first()
        if paiement is None:
            logger.warning(f"Webhook FreeMoPay pour une transaction inconnue : {reference}")
            # 200 quand même : ce n'est pas à FreeMoPay de réessayer indéfiniment
            # pour une transaction qui ne nous concerne pas (ou plus).
            return Response({'detail': 'Transaction inconnue, ignorée.'}, status=status.HTTP_200_OK)

        # On ignore volontairement request.data.get('status') : seule la
        # réponse authentifiée de FreeMoPay fait foi (voir avertissement ci-dessus).
        detail, code = appliquer_statut_freemopay(paiement)
        return Response({'detail': detail}, status=code)
