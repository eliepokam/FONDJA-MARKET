import logging
import uuid

from django.conf import settings
from django.db import transaction
from django.utils import timezone
from rest_framework import generics, mixins, serializers, viewsets, permissions, status
from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework.views import APIView

from django.http import FileResponse, Http404
from rest_framework.parsers import FormParser, JSONParser, MultiPartParser

from core.models import JournalActivite
from core.services import log_action
from .ca import chiffre_affaires
from .models import CompteBancaire, Paiement
from .serializers import CompteBancaireSerializer, PaiementSerializer, STATUTS_LIBERANT_LE_COLIS, VirementPersonnelSerializer
from .validators import TYPES_MIME, valider_preuve
from .services import FreeMoPayError, FREEMOPAY_STATUS_MAP, get_gateway
from notifications.models import Notification
from notifications.services import notify, notify_staff
from shipments.models import Colis
from users.permissions import IsAgent, IsClient, IsAdministrateur

logger = logging.getLogger('django')


def appliquer_statut_freemopay(paiement, gateway=None):
    """
    Interroge FreeMoPay (appel authentifié par nos clés) et met le Paiement à jour.

    Partagé par le webhook ET par l'action « verifier » : si le callback n'arrive jamais
    (tunnel coupé, URL mal configurée...), le front peut quand même faire converger le statut.
    `gateway` permet d'interroger un prestataire précis (ex. l'ancien, après un changement de prestataire).
    Retourne (detail, code_http).
    """
    reference = paiement.reference_prestataire
    try:
        data = (gateway or get_gateway()).get_status(reference)
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
        if self.action in ('create', 'initier', 'preuve'):
            return [IsClient()]
        if self.action in ('valider', 'refuser'):
            return [(IsAgent | IsAdministrateur)()]     # un virement est vérifié par un agent OU un administrateur
        if self.action == 'confirmer':
            return [IsAdministrateur()]
        # Lister / consulter / vérifier un paiement : le client (les siens) et l'administrateur. Un agent n'a pas à voir les montants
        # ni les numéros de paiement des clients.
        return [(IsClient | IsAdministrateur)()]

    @action(detail=True, methods=['post'])
    def confirmer(self, request, pk=None):
        """Confirmation manuelle par un administrateur (cas exceptionnel : règlement reçu hors de la plateforme). Les virements passent par « valider »."""
        paiement = self.get_object()
        paiement.statut = Paiement.Statut.PAYE
        paiement.reference_prestataire = paiement.reference_prestataire or f"MANUEL-{paiement.pk:06d}"
        paiement.paye_le = timezone.now()
        paiement.save()
        return Response({'detail': 'Paiement confirmé manuellement.', 'statut': paiement.statut})

    @action(detail=True, methods=['post'], parser_classes=[MultiPartParser, FormParser])
    def preuve(self, request, pk=None):
        """Le client envoie (ou remplace) la preuve de son virement : photo du reçu ou PDF. Tant qu'elle n'est pas vérifiée."""
        paiement = self.get_object()
        if paiement.methode != Paiement.Methode.VIREMENT:
            return Response({'detail': "Seul un paiement par virement bancaire nécessite une preuve."}, status=status.HTTP_400_BAD_REQUEST)
        if paiement.statut != Paiement.Statut.EN_ATTENTE:
            return Response({'detail': "Ce paiement a déjà été traité : la preuve ne peut plus être modifiée."}, status=status.HTTP_400_BAD_REQUEST)
        fichier = request.FILES.get('preuve')
        if fichier is None:
            return Response({'preuve': ["Joignez la preuve de votre virement (photo du reçu ou PDF)."]}, status=status.HTTP_400_BAD_REQUEST)
        extension = valider_preuve(fichier)
        fichier.name = f"{uuid.uuid4().hex}.{extension}"          # nom imprévisible : jamais le nom d'origine

        ancienne = paiement.preuve.name if paiement.preuve else None
        paiement.preuve = fichier
        paiement.preuve_envoyee_le = timezone.now()
        paiement.reference_virement = str(request.data.get('reference_virement', '') or '').strip()[:100]
        paiement.save(update_fields=['preuve', 'preuve_envoyee_le', 'reference_virement', 'modifie_le'])
        if ancienne:
            paiement.preuve.storage.delete(ancienne)               # la preuve remplacée ne reste pas sur le disque

        montant = f"{int(paiement.montant):,}".replace(',', ' ')
        notify_staff(f"Preuve de virement à vérifier : {montant} XAF pour le colis {paiement.colis.code_suivi} ({paiement.client.nom}).",
                     Notification.Type.PAIEMENT, page='virements')
        return Response(self.get_serializer(paiement).data)

    @action(detail=True, methods=['post'])
    def valider(self, request, pk=None):
        """Un agent ou un administrateur a vérifié la preuve et constaté le virement : le paiement est accepté (et compte dans le chiffre d'affaires)."""
        with transaction.atomic():
            paiement = Paiement.objects.select_for_update().select_related('colis', 'client').get(pk=self.get_object().pk)
            erreur = self._controler_virement_a_verifier(paiement, exiger_preuve=True)
            if erreur:
                return erreur
            paiement.statut = Paiement.Statut.PAYE
            paiement.paye_le = timezone.now()
            paiement.reference_prestataire = paiement.reference_prestataire or f"VIREMENT-{paiement.pk:06d}"
            paiement.verifie_par, paiement.verifie_le, paiement.motif_refus = request.user, paiement.paye_le, ''
            paiement.save()
            if hasattr(request.user, 'administrateur'):
                log_action(request.user.administrateur, JournalActivite.Action.VALIDATION_VIREMENT, paiement.colis.code_suivi,
                           paiement=paiement.pk, montant=str(paiement.montant))
        montant = f"{int(paiement.montant):,}".replace(',', ' ')
        notify(paiement.client, f"Votre virement de {montant} XAF pour le colis {paiement.colis.code_suivi} a été validé. Merci !",
               Notification.Type.PAIEMENT, colis=paiement.colis)
        return Response(self.get_serializer(paiement).data)

    @action(detail=True, methods=['post'])
    def refuser(self, request, pk=None):
        """La preuve est illisible ou le virement introuvable : le paiement est refusé, avec un motif que le client verra. Il peut réessayer."""
        motif = str(request.data.get('motif', '') or '').strip()
        if not motif:
            return Response({'motif': ["Indiquez le motif du refus : le client le verra."]}, status=status.HTTP_400_BAD_REQUEST)
        with transaction.atomic():
            paiement = Paiement.objects.select_for_update().select_related('colis', 'client').get(pk=self.get_object().pk)
            erreur = self._controler_virement_a_verifier(paiement, exiger_preuve=False)
            if erreur:
                return erreur
            paiement.statut = Paiement.Statut.ECHOUE
            paiement.motif_refus = motif[:300]
            paiement.verifie_par, paiement.verifie_le = request.user, timezone.now()
            paiement.save()
            if hasattr(request.user, 'administrateur'):
                log_action(request.user.administrateur, JournalActivite.Action.REFUS_VIREMENT, paiement.colis.code_suivi,
                           paiement=paiement.pk, motif=paiement.motif_refus)
        notify(paiement.client, f"Votre preuve de virement pour le colis {paiement.colis.code_suivi} a été refusée : {paiement.motif_refus} "
                                f"Vous pouvez réessayer.", Notification.Type.PAIEMENT, colis=paiement.colis)
        return Response(self.get_serializer(paiement).data)

    @staticmethod
    def _controler_virement_a_verifier(paiement, exiger_preuve):
        if paiement.methode != Paiement.Methode.VIREMENT:
            return Response({'detail': "Seuls les virements bancaires se vérifient ici."}, status=status.HTTP_400_BAD_REQUEST)
        if paiement.statut != Paiement.Statut.EN_ATTENTE:
            return Response({'detail': "Ce virement a déjà été traité."}, status=status.HTTP_409_CONFLICT)
        if exiger_preuve and not paiement.preuve:
            return Response({'detail': "Le client n'a pas encore envoyé de preuve : il n'y a rien à valider."}, status=status.HTTP_400_BAD_REQUEST)
        return None

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


class PreuveFichierView(APIView):
    """Sert le fichier d'une preuve de virement : au client concerné, aux agents et aux administrateurs uniquement (jamais d'adresse publique)."""

    def get(self, request, pk):
        qs = Paiement.objects.filter(pk=pk)
        if hasattr(request.user, 'client'):
            qs = qs.filter(client=request.user.client)
        elif not (hasattr(request.user, 'agent') or hasattr(request.user, 'administrateur')):
            raise Http404()
        paiement = qs.first()
        if paiement is None or not paiement.preuve:
            raise Http404()
        extension = paiement.preuve.name.rsplit('.', 1)[-1].lower()
        reponse = FileResponse(paiement.preuve.open('rb'), content_type=TYPES_MIME.get(extension, 'application/octet-stream'))
        reponse['X-Content-Type-Options'] = 'nosniff'
        reponse['Cache-Control'] = 'private, no-store'
        return reponse


class VirementsAVerifierView(generics.ListAPIView):
    """File de vérification des virements, pour les agents et les administrateurs. ?etat = a_verifier (défaut) | valides | refuses | tous."""
    serializer_class = VirementPersonnelSerializer
    permission_classes = [(IsAgent | IsAdministrateur)]

    def get_queryset(self):
        qs = Paiement.objects.filter(methode=Paiement.Methode.VIREMENT).select_related('colis', 'client', 'verifie_par')
        etat = self.request.query_params.get('etat', 'a_verifier')
        if etat == 'valides':
            return qs.filter(statut=Paiement.Statut.PAYE).order_by('-verifie_le', '-id')
        if etat == 'refuses':
            return qs.filter(statut=Paiement.Statut.ECHOUE).exclude(motif_refus='').order_by('-verifie_le', '-id')
        if etat == 'tous':
            return qs.order_by('-cree_le')
        # À vérifier : preuve envoyée, pas encore traitée ; le plus ancien d'abord (premier arrivé, premier servi).
        return virements_a_verifier().select_related('colis', 'client', 'verifie_par').order_by('preuve_envoyee_le', 'id')


def virements_a_verifier():
    return Paiement.objects.filter(methode=Paiement.Methode.VIREMENT, statut=Paiement.Statut.EN_ATTENTE, preuve__isnull=False).exclude(preuve='')


class ComptesBancairesView(generics.ListAPIView):
    """Comptes sur lesquels faire le virement : les comptes ACTIFS, pour tout compte connecté."""
    serializer_class = CompteBancaireSerializer
    permission_classes = [permissions.IsAuthenticated]
    pagination_class = None

    def get_queryset(self):
        return CompteBancaire.objects.filter(actif=True)


class AdminCompteBancaireViewSet(viewsets.ModelViewSet):
    """Administrateur : gérer les comptes bancaires (y compris désactivés). Chaque modification est inscrite au journal."""
    serializer_class = CompteBancaireSerializer
    permission_classes = [IsAdministrateur]
    queryset = CompteBancaire.objects.all()
    pagination_class = None

    def _journal(self, compte, **details):
        log_action(self.request.user.administrateur, JournalActivite.Action.MODIFICATION_COMPTE_BANCAIRE, f"{compte.banque} — {compte.titulaire}", **details)

    def perform_create(self, serializer):
        compte = serializer.save()
        self._journal(compte, operation='création')

    def perform_update(self, serializer):
        avant = CompteBancaire.objects.get(pk=serializer.instance.pk)
        compte = serializer.save()
        changements = [c for c in ('banque', 'titulaire', 'numero_compte', 'iban', 'code_swift', 'agence', 'instructions', 'actif')
                       if getattr(avant, c) != getattr(compte, c)]
        self._journal(compte, operation='modification', champs=changements)   # les valeurs ne sont pas copiées dans le journal

    def perform_destroy(self, instance):
        self._journal(instance, operation='suppression')
        instance.delete()


class ChiffreAffairesView(APIView):
    """Administrateur : chiffre d'affaires mensuel, annuel ou global. ?periode=mois&annee=2026&mois=11 | ?periode=annee&annee=2026 | ?periode=global."""
    permission_classes = [IsAdministrateur]

    def get(self, request):
        p = request.query_params
        periode = p.get('periode', 'mois')
        if periode not in ('mois', 'annee', 'global'):
            return Response({'periode': ["Choisissez « mois », « annee » ou « global »."]}, status=status.HTTP_400_BAD_REQUEST)
        try:
            annee = int(p['annee']) if p.get('annee') else None
            mois = int(p['mois']) if p.get('mois') else None
        except ValueError:
            return Response({'detail': "L'année et le mois doivent être des nombres."}, status=status.HTTP_400_BAD_REQUEST)
        if (annee is not None and not 2000 <= annee <= 2100) or (mois is not None and not 1 <= mois <= 12):
            return Response({'detail': "Année ou mois hors limites."}, status=status.HTTP_400_BAD_REQUEST)
        return Response(chiffre_affaires(periode, annee, mois))
