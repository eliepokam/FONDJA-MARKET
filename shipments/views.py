from decimal import Decimal
from django.db.models import Sum
from django.db.models.functions import Coalesce
from .conteneurs import enregistrer_pour, enregistrer_remplissage, evolution, pourcentage, variation
from rest_framework.response import Response
from rest_framework import viewsets, permissions, status
from rest_framework.decorators import action
from rest_framework import generics
from rest_framework.permissions import AllowAny
from django.utils.cache import patch_cache_control
from django.db import transaction
from django.db.models import Count
from .models import AdresseTransit, Colis, LotDedouanement, Tarif, Incident
from .serializers import AdresseTransitSerializer, ColisSerializer, ColisAgentSerializer, LotDedouanementSerializer, TarifSerializer, IncidentSerializer, PublicTarifSerializer
from .utils import generate_code_suivi
from .status import TransitionInvalide, changer_statut
from users.permissions import IsClient, IsAdministrateur, IsAgent
from rest_framework.exceptions import APIException, PermissionDenied
from django.db.models import Q
from django.db.models.deletion import ProtectedError, RestrictedError
from notifications.services import notify
from notifications.models import Notification
from rest_framework.exceptions import PermissionDenied
from django.shortcuts import get_object_or_404
from rest_framework.views import APIView
from django.utils.cache import add_never_cache_headers
from tracking.models import HistoriqueStatutColis
from datetime import timedelta
from django.utils import timezone
from core.services import log_action
from core.models import JournalActivite


class ColisNonSupprimable(APIException):
    status_code = 409
    default_detail = "Ce colis a un historique de paiement : il ne peut pas être supprimé."
    default_code = 'conflict'


class ColisViewSet(viewsets.ModelViewSet):
    serializer_class = ColisSerializer
    filterset_fields = ['statut', 'mode_transport']
    search_fields = ['code_suivi', 'client__telephone', 'client__nom']

    def get_queryset(self):
        user = self.request.user
        qs = Colis.objects.filter(client=user.client) if hasattr(user, 'client') else Colis.objects.all()

        lot_filter = self.request.query_params.get('lot')
        if lot_filter == 'aucun':
            qs = qs.filter(lot__isnull=True)
        elif lot_filter == 'assigne':
            qs = qs.filter(lot__isnull=False)

        # ?en_cours=true : tous les colis non livrés (le filtre « statut » ne sait cibler qu'une seule valeur)
        if self.request.query_params.get('en_cours') == 'true':
            qs = qs.exclude(statut=Colis.Statut.LIVRE)

        return qs
    
    def perform_create(self, serializer):
        serializer.save(client=self.request.user.client, code_suivi=generate_code_suivi())

    def perform_update(self, serializer):
        user = self.request.user
        instance = serializer.instance
        if hasattr(user, 'client') and instance.statut != Colis.Statut.DECLAREE:
            raise PermissionDenied("Ce colis a déjà été pris en charge, il ne peut plus être modifié.")
        serializer.save()

    def perform_destroy(self, instance):
        user = self.request.user
        is_owner_client = hasattr(user, 'client') and instance.client_id == user.client.id
        if is_owner_client and instance.statut != Colis.Statut.DECLAREE:
            raise PermissionDenied("Ce colis a déjà été pris en charge, il ne peut plus être supprimé.")
        try:
            instance.delete()
        except (RestrictedError, ProtectedError):
            # Un colis avec un historique de paiement ne se supprime pas (traçabilité) : message clair, pas une erreur 500.
            raise ColisNonSupprimable()

    def get_serializer_class(self):
        user = self.request.user
        if self.action in ('update', 'partial_update') and (hasattr(user, 'agent') or hasattr(user, 'administrateur')):
            return ColisAgentSerializer
        return ColisSerializer

    def get_permissions(self):
        if self.action == 'create':
            return [IsClient()]
        if self.action in ('update', 'partial_update'):
            return [(IsClient | IsAgent | IsAdministrateur)()]   # le client : seulement SON colis, tant qu'il est « déclaré » (voir perform_update)
        if self.action == 'destroy':
            return [(IsClient | IsAdministrateur)()]          # un agent ne supprime JAMAIS un colis (traces, paiements, douane)
        return [permissions.IsAuthenticated()]
    def retrieve(self, request, *args, **kwargs):
        response = super().retrieve(request, *args, **kwargs)
        add_never_cache_headers(response)
        return response


class ConteneurConflit(APIException):
    status_code = 409
    default_detail = "Action impossible dans l'état actuel du conteneur."
    default_code = 'conteneur_conflit'


class LotDedouanementViewSet(viewsets.ModelViewSet):
    """
    Conteneurs (lots d'embarquement). L'administrateur les crée, les modifie, les clôture et les rouvre. Les agents ne voient que les conteneurs
    EN CHARGEMENT (un conteneur clôturé est « retiré » de leur espace) et ne peuvent rien y modifier.
    """
    serializer_class = LotDedouanementSerializer

    def get_queryset(self):
        qs = LotDedouanement.objects.annotate(
            nb_colis=Count('colis'),
            nb_livres=Count('colis', filter=Q(colis__statut=Colis.Statut.LIVRE)),
            volume_total=Sum(Coalesce('colis__volume_m3', 'colis__volume_declare_m3')),
            nb_sans_volume=Count('colis', filter=Q(colis__volume_m3__isnull=True, colis__volume_declare_m3__isnull=True)),
        ).order_by('-cree_le')
        if not hasattr(self.request.user, 'administrateur'):
            return qs.filter(statut=LotDedouanement.Statut.OUVERT)
        statut = self.request.query_params.get('statut')
        return qs.filter(statut=statut) if statut in LotDedouanement.Statut.values else qs

    def get_permissions(self):
        if self.request.method in permissions.SAFE_METHODS:   # GET, HEAD, OPTIONS
            return [(IsAdministrateur | IsAgent)()]
        return [IsAdministrateur()]

    def perform_create(self, serializer):
        lot = serializer.save(administrateur=self.request.user.administrateur)
        enregistrer_remplissage(lot, forcer=True)            # point de départ de la courbe d'évolution (0 %)
        log_action(self.request.user.administrateur, JournalActivite.Action.CREATION_LOT, lot.numero_besc,
                   type=lot.get_type_conteneur_display(), capacite_m3=f"{lot.capacite_m3:.2f}")

    def perform_update(self, serializer):
        if serializer.instance.statut == LotDedouanement.Statut.CLOTURE:
            raise ConteneurConflit("Ce conteneur est clôturé : rouvrez-le avant de le modifier.")
        avant = LotDedouanement.objects.get(pk=serializer.instance.pk)
        lot = serializer.save()
        changements = {c: f"{getattr(avant, c)} → {getattr(lot, c)}" for c in ('numero_besc', 'type_conteneur', 'capacite_m3') if getattr(avant, c) != getattr(lot, c)}
        if changements:
            enregistrer_remplissage(lot, forcer=True)         # le pourcentage change si la capacité change
            log_action(self.request.user.administrateur, JournalActivite.Action.MODIFICATION_LOT, lot.numero_besc, **changements)

    def perform_destroy(self, instance):
        if instance.statut == LotDedouanement.Statut.CLOTURE:
            raise ConteneurConflit("Un conteneur clôturé est conservé en archive : il ne peut pas être supprimé.")
        description = f"{instance.numero_besc} ({instance.colis.count()} colis détachés)"
        instance.delete()
        log_action(self.request.user.administrateur, JournalActivite.Action.SUPPRESSION_LOT, description)

    @action(detail=True, methods=['post'])
    def cloturer(self, request, pk=None):
        """
        Clôture le conteneur (rempli ET livré) : il quitte le tableau de bord et l'espace des agents, et n'accepte plus de colis.
        S'il reste des colis non livrés, il faut confirmer explicitement avec « forcer ».
        """
        lot = self.get_object()
        if lot.statut == LotDedouanement.Statut.CLOTURE:
            raise ConteneurConflit("Ce conteneur est déjà clôturé.")
        non_livres = lot.nb_colis - lot.nb_livres
        forcer = str(request.data.get('forcer', '')).lower() in ('1', 'true', 'oui')
        pct = float(pourcentage(Decimal(lot.volume_total or 0), lot.capacite_m3))
        if non_livres and not forcer:
            return Response({
                'detail': f"{non_livres} colis de ce conteneur ne sont pas encore livrés. Confirmez pour le clôturer malgré tout.",
                'code': 'colis_non_livres', 'non_livres': non_livres, 'pourcentage': pct,
            }, status=409)
        lot.statut = LotDedouanement.Statut.CLOTURE
        lot.cloture_le = timezone.now()
        lot.cloture_par = request.user.administrateur
        lot.save(update_fields=['statut', 'cloture_le', 'cloture_par'])
        enregistrer_remplissage(lot, forcer=True)
        log_action(request.user.administrateur, JournalActivite.Action.CLOTURE_LOT, lot.numero_besc,
                   pourcentage=pct, colis=lot.nb_colis, non_livres=non_livres)
        return Response(self.get_serializer(self.get_queryset().get(pk=lot.pk)).data)

    @action(detail=True, methods=['post'])
    def rouvrir(self, request, pk=None):
        lot = self.get_object()
        if lot.statut != LotDedouanement.Statut.CLOTURE:
            raise ConteneurConflit("Ce conteneur n'est pas clôturé.")
        lot.statut = LotDedouanement.Statut.OUVERT
        lot.cloture_le = None
        lot.cloture_par = None
        lot.save(update_fields=['statut', 'cloture_le', 'cloture_par'])
        enregistrer_remplissage(lot, forcer=True)
        log_action(request.user.administrateur, JournalActivite.Action.REOUVERTURE_LOT, lot.numero_besc)
        return Response(self.get_serializer(self.get_queryset().get(pk=lot.pk)).data)

    @action(detail=True, methods=['get'])
    def evolution(self, request, pk=None):
        """Courbe d'évolution du remplissage (les 60 derniers changements)."""
        lot = self.get_object()
        return Response({'variation_7j': float(variation(lot)), 'points': [
            {**p, 'pourcentage': float(p['pourcentage']), 'volume_m3': float(p['volume_m3'])} for p in evolution(lot, 60)]})


class TarifViewSet(viewsets.ModelViewSet):
    serializer_class = TarifSerializer

    def get_queryset(self):
        user = self.request.user
        if hasattr(user, 'client'):
            return Tarif.objects.filter(actif=True)
        return Tarif.objects.all().order_by('mode_transport', '-actif', '-cree_le')

    @staticmethod
    def _desactiver_les_autres(tarif):
        """Un seul tarif ACTIF par mode de transport : activer celui-ci désactive les autres du même mode."""
        if tarif.actif:
            Tarif.objects.filter(mode_transport=tarif.mode_transport, actif=True).exclude(pk=tarif.pk).update(actif=False)

    def perform_create(self, serializer):
        with transaction.atomic():
            tarif = serializer.save(administrateur=self.request.user.administrateur)
            self._desactiver_les_autres(tarif)
        log_action(
            self.request.user.administrateur, JournalActivite.Action.CREATION_TARIF,
            tarif.get_mode_transport_display(),
            prix_par_kg=str(tarif.prix_par_kg) if tarif.prix_par_kg else None,
            prix_par_m3=str(tarif.prix_par_m3) if tarif.prix_par_m3 else None,
        )

    def perform_update(self, serializer):
        ancien = Tarif.objects.get(pk=serializer.instance.pk)
        with transaction.atomic():
            tarif = serializer.save()
            self._desactiver_les_autres(tarif)
        # On journalise ce qui a VRAIMENT changé (prix, délais, activation), pas seulement le prix.
        changements = {}
        for champ in ('prix_par_kg', 'prix_par_m3', 'delai_min_jours', 'delai_max_jours', 'actif'):
            avant, apres = getattr(ancien, champ), getattr(tarif, champ)
            if avant != apres:
                changements[champ] = f"{avant} → {apres}"
        log_action(self.request.user.administrateur, JournalActivite.Action.MODIFICATION_TARIF,
                   tarif.get_mode_transport_display(), **changements)

    def perform_destroy(self, instance):
        description = instance.get_mode_transport_display()
        log_action(self.request.user.administrateur, JournalActivite.Action.SUPPRESSION_TARIF, description)
        instance.delete()

    def get_permissions(self):
        if self.request.method in permissions.SAFE_METHODS:
            return [permissions.IsAuthenticated()]
        return [IsAdministrateur()]


class PublicTarifListView(generics.ListAPIView):
    """
    Tarifs ACTIFS, lisibles sans compte : alimente la section « Tarifs » de la landing page.
    Lecture seule, mise en cache 5 minutes, sans authentification ni limitation de débit (donnée publique, peu coûteuse).
    """
    permission_classes = [AllowAny]
    authentication_classes = []
    throttle_classes = []
    pagination_class = None
    filter_backends = []
    serializer_class = PublicTarifSerializer

    def get_queryset(self):
        return Tarif.objects.filter(actif=True).order_by('mode_transport', '-cree_le')

    def list(self, request, *args, **kwargs):
        response = super().list(request, *args, **kwargs)
        patch_cache_control(response, public=True, max_age=300)
        return response


class IncidentViewSet(viewsets.ModelViewSet):
    serializer_class = IncidentSerializer
    filterset_fields = ['type', 'gravite', 'colis', 'lot']

    def get_queryset(self):
        qs = Incident.objects.all().order_by('-cree_le')
        user = self.request.user
        if hasattr(user, 'client'):
            # Un client ne voit que les incidents de SES colis (ou d'un lot qui en contient) : jusqu'ici il
            # recevait tous les incidents de tous les clients, descriptions comprises.
            qs = qs.filter(Q(colis__client=user.client) | Q(lot__colis__client=user.client)).distinct()
        etat = self.request.query_params.get('etat')
        if etat == 'actif':
            qs = qs.filter(resolu_le__isnull=True)
        elif etat == 'resolu':
            qs = qs.filter(resolu_le__isnull=False)
        return qs

    @staticmethod
    def _colis_concernes(incident):
        if incident.colis_id:
            return [incident.colis]
        if incident.lot_id:
            return list(incident.lot.colis.select_related('client'))
        return []

    def perform_create(self, serializer):
        incident = serializer.save(declare_par=self.request.user)
        for colis in self._colis_concernes(incident):
            texte = (
                f"Imprévu signalé sur votre colis {colis.code_suivi} : {incident.get_type_display()}"
                if incident.colis_id else
                f"Imprévu signalé sur le lot {incident.lot.numero_besc} : {incident.get_type_display()}"
            )
            notify(colis.client, texte, Notification.Type.INCIDENT, colis=colis)

    @action(detail=True, methods=['post'])
    def resoudre(self, request, pk=None):
        """Clôture l'imprévu et prévient les clients concernés (sinon « Imprévu signalé » restait affiché pour toujours)."""
        incident = self.get_object()
        if incident.resolu_le is not None:
            return Response({'detail': "Cet imprévu est déjà résolu."}, status=status.HTTP_409_CONFLICT)
        incident.resolu_le = timezone.now()
        incident.save(update_fields=['resolu_le'])
        for colis in self._colis_concernes(incident):
            notify(
                colis.client,
                f"L'imprévu signalé sur votre colis {colis.code_suivi} est résolu ({incident.get_type_display()}).",
                Notification.Type.INCIDENT,
                colis=colis,
            )
        return Response(self.get_serializer(incident).data)

    def get_permissions(self):
        if self.request.method in permissions.SAFE_METHODS:
            return [permissions.IsAuthenticated()]
        return [(IsAgent | IsAdministrateur)()]


def _identifiants(valeur):
    """Liste d'identifiants entiers valide, sinon None (évite une erreur 500 sur `id__in=['abc']`)."""
    if not isinstance(valeur, list) or not valeur:
        return None
    try:
        return [int(v) for v in valeur]
    except (TypeError, ValueError):
        return None


class BulkAssignLotView(APIView):
    permission_classes = [(IsAgent | IsAdministrateur)]

    def post(self, request):
        colis_ids = _identifiants(request.data.get('colis_ids'))
        lot_id = request.data.get('lot_id')
        if not colis_ids or not lot_id:
            return Response({'detail': "colis_ids (liste d'identifiants) et lot_id sont requis."}, status=400)

        lot = get_object_or_404(LotDedouanement, pk=lot_id)
        if lot.statut == LotDedouanement.Statut.CLOTURE:
            return Response({'detail': f"Le conteneur {lot.numero_besc} est clôturé : plus aucun colis ne peut y être ajouté. "
                                       "Un administrateur peut le rouvrir."}, status=409)

        valides, ignores = [], []
        for colis in Colis.objects.filter(id__in=colis_ids).select_related('lot'):
            if colis.lot_id == lot.id:
                ignores.append({'code_suivi': colis.code_suivi, 'raison': "Déjà dans ce conteneur."})
            elif colis.lot is not None and colis.lot.statut == LotDedouanement.Statut.CLOTURE:
                ignores.append({'code_suivi': colis.code_suivi, 'raison': f"Dans le conteneur clôturé {colis.lot.numero_besc} : il n'en sort plus."})
            elif colis.statut == Colis.Statut.LIVRE:
                ignores.append({'code_suivi': colis.code_suivi, 'raison': "Colis déjà livré."})
            else:
                valides.append(colis.id)

        anciens = set(Colis.objects.filter(id__in=valides).values_list('lot_id', flat=True))
        updated = Colis.objects.filter(id__in=valides).update(lot=lot)
        enregistrer_pour({lot.id, *anciens})                 # .update() ne déclenche pas les signaux : on photographie ici
        detail = f'{updated} colis assignés.' + (f' {len(ignores)} ignoré(s).' if ignores else '')
        return Response({'detail': detail, 'count': updated, 'ignores': ignores})


class BulkChangeStatusView(APIView):
    permission_classes = [(IsAgent | IsAdministrateur)]

    def post(self, request):
        nouveau_statut = request.data.get('statut')
        note = str(request.data.get('note', ''))[:255]
        if nouveau_statut not in Colis.Statut.values:
            return Response({'detail': 'Statut invalide.'}, status=400)

        lot_id = request.data.get('lot_id')
        colis_ids = _identifiants(request.data.get('colis_ids'))
        if lot_id:
            colis_qs = Colis.objects.filter(lot_id=lot_id)
        elif colis_ids:
            colis_qs = Colis.objects.filter(id__in=colis_ids)
        else:
            return Response({'detail': 'lot_id ou colis_ids (liste d\'identifiants) requis.'}, status=400)

        count = 0
        ignores = []
        for colis in colis_qs.select_related('client'):
            try:
                changer_statut(colis, nouveau_statut, request.user, note)
                count += 1
            except TransitionInvalide as exc:
                # Un colis déjà au bon statut (ou qu'on ne peut pas faire reculer) n'empêche pas de traiter les autres.
                ignores.append({'code_suivi': colis.code_suivi, 'raison': str(exc)})

        detail = f'{count} colis mis à jour.' + (f' {len(ignores)} ignoré(s).' if ignores else '')
        return Response({'detail': detail, 'count': count, 'ignores': ignores})


def _contact_support():
    """Coordonnées du support, lues dans les réglages du serveur (variables d'environnement) : modifiables sans reconstruire le front."""
    from django.conf import settings
    chiffres = ''.join(c for c in str(getattr(settings, 'SUPPORT_WHATSAPP', '') or '') if c.isdigit())
    return {
        'whatsapp': chiffres,
        'email': str(getattr(settings, 'SUPPORT_EMAIL', '') or ''),
        'horaires': str(getattr(settings, 'SUPPORT_HORAIRES', '') or ''),
    }


class CoordonneesView(APIView):
    """Nos coordonnées : adresses de réception en Chine (une par mode de transport) et contact du support. Lecture pour tout compte connecté."""
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request):
        adresses = AdresseTransit.objects.filter(actif=True)
        return Response({'adresses': AdresseTransitSerializer(adresses, many=True).data, 'contact': _contact_support()})


class AdminAdressesTransitListView(generics.ListAPIView):
    """Administrateur : toutes les adresses de transit (y compris désactivées)."""
    permission_classes = [IsAdministrateur]
    serializer_class = AdresseTransitSerializer
    queryset = AdresseTransit.objects.all()
    pagination_class = None


class AdminAdresseTransitDetailView(generics.RetrieveUpdateAPIView):
    """Administrateur : modifier l'adresse d'un mode (aerien / maritime). L'action est inscrite au journal d'activité."""
    permission_classes = [IsAdministrateur]
    serializer_class = AdresseTransitSerializer
    queryset = AdresseTransit.objects.all()
    lookup_field = 'mode'
    http_method_names = ['get', 'patch', 'put', 'head', 'options']

    def perform_update(self, serializer):
        avant = AdresseTransit.objects.get(pk=serializer.instance.pk)
        adresse = serializer.save()
        changements = {c: f"{getattr(avant, c)} → {getattr(adresse, c)}" for c in ('adresse', 'destinataire', 'telephone', 'marquage', 'instructions', 'actif')
                       if getattr(avant, c) != getattr(adresse, c)}
        log_action(self.request.user.administrateur, JournalActivite.Action.MODIFICATION_ADRESSE_TRANSIT, adresse.get_mode_display(), **changements)
