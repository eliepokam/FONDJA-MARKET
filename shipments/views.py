from rest_framework.response import Response
from rest_framework import viewsets, permissions, status
from rest_framework.decorators import action
from django.db.models import Count
from .models import Colis, LotDedouanement, Tarif, Incident
from .serializers import ColisSerializer, ColisAgentSerializer, LotDedouanementSerializer, TarifSerializer, IncidentSerializer
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
            return [(IsAgent | IsAdministrateur)()]
        if self.action == 'destroy':
            return [(IsClient | IsAgent | IsAdministrateur)()]
        return [permissions.IsAuthenticated()]
    def retrieve(self, request, *args, **kwargs):
        response = super().retrieve(request, *args, **kwargs)
        add_never_cache_headers(response)
        return response


class LotDedouanementViewSet(viewsets.ModelViewSet):
    queryset = LotDedouanement.objects.annotate(nb_colis=Count('colis')).order_by('-cree_le')
    serializer_class = LotDedouanementSerializer

    def perform_create(self, serializer):
        serializer.save(administrateur=self.request.user.administrateur)

    def get_permissions(self):
        if self.request.method in permissions.SAFE_METHODS:   # GET, HEAD, OPTIONS
            return [(IsAdministrateur | IsAgent)()]
        return [IsAdministrateur()]

class TarifViewSet(viewsets.ModelViewSet):
    serializer_class = TarifSerializer

    def get_queryset(self):
        user = self.request.user
        if hasattr(user, 'client'):
            return Tarif.objects.filter(actif=True)
        return Tarif.objects.all()

    def perform_create(self, serializer):
        tarif = serializer.save(administrateur=self.request.user.administrateur)
        log_action(
            self.request.user.administrateur, JournalActivite.Action.CREATION_TARIF,
            tarif.get_mode_transport_display(),
            prix_par_kg=str(tarif.prix_par_kg) if tarif.prix_par_kg else None,
            prix_par_m3=str(tarif.prix_par_m3) if tarif.prix_par_m3 else None,
        )

    def perform_update(self, serializer):
        ancien = self.get_object()
        ancien_prix = str(ancien.prix_par_kg or ancien.prix_par_m3)
        tarif = serializer.save()
        nouveau_prix = str(tarif.prix_par_kg or tarif.prix_par_m3)
        log_action(
            self.request.user.administrateur, JournalActivite.Action.MODIFICATION_TARIF,
            tarif.get_mode_transport_display(), ancien_prix=ancien_prix, nouveau_prix=nouveau_prix,
        )

    def perform_destroy(self, instance):
        description = instance.get_mode_transport_display()
        log_action(self.request.user.administrateur, JournalActivite.Action.SUPPRESSION_TARIF, description)
        instance.delete()

    def get_permissions(self):
        if self.request.method in permissions.SAFE_METHODS:
            return [permissions.IsAuthenticated()]
        return [IsAdministrateur()]


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
        updated = Colis.objects.filter(id__in=colis_ids).update(lot=lot)
        return Response({'detail': f'{updated} colis assignés.', 'count': updated})

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
