import csv
from datetime import datetime, timedelta

from django.db.models import Count, F, Sum
from django.db.models.functions import Coalesce
from django.http import HttpResponse
from django.utils import timezone
from rest_framework import generics
from rest_framework.response import Response
from rest_framework.views import APIView

from complaints.models import Reclamation
from payments.models import Paiement
from shipments.models import Colis
from tracking.models import HistoriqueStatutColis
from users.permissions import IsAdministrateur, IsAgent
from .models import JournalActivite
from .serializers import JournalActiviteSerializer
from .services import log_action


class JournalActiviteListView(generics.ListAPIView):
    serializer_class = JournalActiviteSerializer
    permission_classes = [IsAdministrateur]

    def get_queryset(self):
        # Tout l'historique, paginé : avant, un `[:100]` figé et la 1re page seule (20 lignes) étaient visibles.
        return JournalActivite.objects.select_related('auteur').all()


class AdminStatsView(APIView):
    permission_classes = [(IsAgent | IsAdministrateur)]

    def get(self, request):
        now = timezone.localtime()                      # heure du Cameroun : « aujourd'hui » et « ce mois » doivent suivre le calendrier local
        today = now.date()
        month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)

        colis_en_cours = Colis.objects.exclude(statut=Colis.Statut.LIVRE).count()
        colis_en_retard = Colis.objects.exclude(statut=Colis.Statut.LIVRE).filter(
            date_livraison_estimee__lt=today
        ).count()

        ca_du_mois = Paiement.objects.filter(
            statut='paye', paye_le__gte=month_start
        ).aggregate(total=Sum('montant'))['total'] or 0

        reclamations_ouvertes = Reclamation.objects.filter(statut='ouverte').count()

        stock_chine = Colis.objects.filter(statut=Colis.Statut.RECU_ENTREPOT).count()
        stock_douala = Colis.objects.filter(statut=Colis.Statut.ARRIVE_DEPOT_LOCAL).count()

        entrees_aujourdhui = Colis.objects.filter(cree_le__date=today).count()
        # Une « sortie » = un colis dont on a ENREGISTRÉ la livraison aujourd'hui. Avant, on comptait tout colis livré
        # modifié aujourd'hui, donc la moindre retouche d'un ancien colis livré gonflait le chiffre.
        sorties_aujourdhui = (HistoriqueStatutColis.objects
                              .filter(statut=Colis.Statut.LIVRE, cree_le__date=today)
                              .values('colis').distinct().count())

        comptes = dict(Colis.objects.values_list('statut').annotate(n=Count('id')))
        colis_par_etape = {valeur: comptes.get(valeur, 0) for valeur, _ in Colis.Statut.choices}

        cbm_en_attente = Colis.objects.filter(
            mode_transport=Colis.ModeTransport.MARITIME,
            statut__in=[Colis.Statut.DECLAREE, Colis.Statut.RECU_ENTREPOT],
        ).aggregate(total=Sum(Coalesce('volume_m3', 'volume_declare_m3')))['total'] or 0

        return Response({
            'colis_en_cours': colis_en_cours,
            'colis_en_retard': colis_en_retard,
            'ca_du_mois': float(ca_du_mois),
            'reclamations_ouvertes': reclamations_ouvertes,
            'stock_chine': stock_chine,
            'stock_douala': stock_douala,
            'entrees_aujourdhui': entrees_aujourdhui,
            'sorties_aujourdhui': sorties_aujourdhui,
            'colis_par_etape': colis_par_etape,
            'cbm_en_attente': float(cbm_en_attente),
            'cbm_capacite': 33,
        })


TYPES_EXPORT = {'colis', 'paiements', 'reclamations'}


class ExportDataView(APIView):
    permission_classes = [IsAdministrateur]

    @staticmethod
    def _date(valeur):
        try:
            return datetime.strptime(valeur, '%Y-%m-%d').date() if valeur else None
        except ValueError:
            return False

    def get(self, request):
        start_date = self._date(request.query_params.get('start'))
        end_date = self._date(request.query_params.get('end'))
        if start_date is False or end_date is False:
            return Response({'detail': "Dates invalides : utilise le format AAAA-MM-JJ."}, status=400)
        if start_date and end_date and start_date > end_date:
            return Response({'detail': "La date de début doit précéder la date de fin."}, status=400)

        types = [t for t in request.query_params.get('types', '').split(',') if t]
        inconnus = [t for t in types if t not in TYPES_EXPORT]
        if not types or inconnus:
            return Response({'detail': "Choisis au moins un type de contenu parmi : colis, paiements, reclamations."}, status=400)

        # Fichier CSV lisible directement par Excel : BOM UTF-8 écrit UNE fois (avant, `charset=utf-8-sig` en
        # ajoutait un à chaque ligne) et séparateur « ; » (Excel en français ouvrait tout dans la première colonne).
        response = HttpResponse(content_type='text/csv; charset=utf-8')
        response['Content-Disposition'] = f'attachment; filename="export_fondja_{start_date or "debut"}_{end_date or "fin"}.csv"'
        response.write('\ufeff')
        writer = csv.writer(response, delimiter=';')

        if 'colis' in types:
            self._write_colis(writer, start_date, end_date)
        if 'paiements' in types:
            self._write_paiements(writer, start_date, end_date)
        if 'reclamations' in types:
            self._write_reclamations(writer, start_date, end_date)

        # Un export contient des données personnelles : on garde la trace de qui l'a fait et sur quelle période.
        log_action(request.user.administrateur, JournalActivite.Action.EXPORT_DONNEES, ', '.join(types),
                   debut=str(start_date) if start_date else None, fin=str(end_date) if end_date else None)
        return response

    @staticmethod
    def _jour(valeur):
        return timezone.localtime(valeur).strftime('%d/%m/%Y') if valeur else ''

    def _write_colis(self, writer, start_date, end_date):
        qs = Colis.objects.select_related('client').all()
        if start_date:
            qs = qs.filter(cree_le__date__gte=start_date)
        if end_date:
            qs = qs.filter(cree_le__date__lte=end_date)

        writer.writerow(['=== COLIS ==='])
        writer.writerow(['Code suivi', 'Client', 'Statut', 'Mode transport', 'Coût final', 'Créé le'])
        for c in qs:
            writer.writerow([
                c.code_suivi,
                self.sanitize_csv(c.client.nom),
                c.get_statut_display(), c.get_mode_transport_display(),
                c.cout_final or '', self._jour(c.cree_le),
            ])
        writer.writerow([])

    def _write_paiements(self, writer, start_date, end_date):
        qs = Paiement.objects.select_related('client', 'colis').filter(statut='paye')
        if start_date:
            qs = qs.filter(paye_le__date__gte=start_date)
        if end_date:
            qs = qs.filter(paye_le__date__lte=end_date)

        writer.writerow(['=== PAIEMENTS ==='])
        writer.writerow(['Client', 'Colis', 'Montant', 'Méthode', 'Payé le'])
        for p in qs:
            writer.writerow([
                self.sanitize_csv(p.client.nom), p.colis.code_suivi, p.montant,
                p.get_methode_display(), self._jour(p.paye_le),
            ])
        writer.writerow([])

    def _write_reclamations(self, writer, start_date, end_date):
        qs = Reclamation.objects.select_related('client', 'colis').all()
        if start_date:
            qs = qs.filter(cree_le__date__gte=start_date)
        if end_date:
            qs = qs.filter(cree_le__date__lte=end_date)

        writer.writerow(['=== RÉCLAMATIONS ==='])
        writer.writerow(['Client', 'Colis', 'Type', 'Statut', 'Créée le'])
        for r in qs:
            writer.writerow([
                self.sanitize_csv(r.client.nom), r.colis.code_suivi,
                r.get_type_display(), r.get_statut_display(), self._jour(r.cree_le),
            ])

    def sanitize_csv(self, value):
        s = str(value) if value is not None else ''
        if s and s[0] in ('=', '+', '-', '@'):
            return f"'{s}"
        return s
