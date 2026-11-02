import csv
from datetime import datetime
from django.http import HttpResponse
from django.db.models import Count, Sum
from django.utils import timezone
from rest_framework.views import APIView
from rest_framework.response import Response
from shipments.models import Colis
from payments.models import Paiement
from complaints.models import Reclamation
from users.permissions import IsAdministrateur, IsAgent
from rest_framework import generics
from .models import JournalActivite
from .serializers import JournalActiviteSerializer

class JournalActiviteListView(generics.ListAPIView):
    serializer_class = JournalActiviteSerializer
    permission_classes = [IsAdministrateur]

    def get_queryset(self):
        return JournalActivite.objects.all()[:100]
    
class AdminStatsView(APIView):
    permission_classes = [(IsAgent | IsAdministrateur)]

    def get(self, request):
        now = timezone.now()
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
        sorties_aujourdhui = Colis.objects.filter(statut=Colis.Statut.LIVRE, modifie_le__date=today).count()

        # Une seule requête, et TOUS les statuts : le tableau de bord de l'agent affiche « À réceptionner »
        # (= déclarés), qui n'était jamais renvoyé et restait donc bloqué à 0.
        comptes = dict(Colis.objects.values_list('statut').annotate(n=Count('id')))
        colis_par_etape = {valeur: comptes.get(valeur, 0) for valeur, _ in Colis.Statut.choices}

        maritime_en_attente = Colis.objects.filter(
            mode_transport=Colis.ModeTransport.MARITIME,
            statut__in=[Colis.Statut.DECLAREE, Colis.Statut.RECU_ENTREPOT],
        )
        cbm_en_attente = sum(float(c.volume_m3 or c.volume_declare_m3 or 0) for c in maritime_en_attente)

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
            'cbm_en_attente': cbm_en_attente,
            'cbm_capacite': 33,
        })

class ExportDataView(APIView):
    permission_classes = [IsAdministrateur]

    def get(self, request):
        start = request.query_params.get('start')
        end = request.query_params.get('end')
        types = request.query_params.get('types', '').split(',')
        start_date = datetime.strptime(start, '%Y-%m-%d').date() if start else None
        end_date = datetime.strptime(end, '%Y-%m-%d').date() if end else None

        response = HttpResponse(content_type='text/csv; charset=utf-8-sig')
        response['Content-Disposition'] = f'attachment; filename="export_fondja_{start}_{end}.csv"'
        writer = csv.writer(response)

        if 'colis' in types:
            self._write_colis(writer, start_date, end_date)
        if 'paiements' in types:
            self._write_paiements(writer, start_date, end_date)
        if 'reclamations' in types:
            self._write_reclamations(writer, start_date, end_date)

        return response

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
                self.sanitize_csv(c.client.nom),   # ← seul champ texte libre ici
                c.get_statut_display(), c.get_mode_transport_display(),
                c.cout_final or '', c.cree_le.strftime('%d/%m/%Y'),
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
                p.get_methode_display(), p.paye_le.strftime('%d/%m/%Y') if p.paye_le else '',
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
                r.get_type_display(), r.get_statut_display(), r.cree_le.strftime('%d/%m/%Y'),
            ])

    def sanitize_csv(self, value):
        s = str(value) if value is not None else ''
        if s and s[0] in ('=', '+', '-', '@'):
            return f"'{s}"
        return s