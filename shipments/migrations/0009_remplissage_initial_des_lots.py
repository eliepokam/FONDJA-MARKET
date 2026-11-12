from decimal import Decimal

from django.db import migrations
from django.db.models import Sum
from django.db.models.functions import Coalesce


def photographier(apps, schema_editor):
    """Les conteneurs déjà créés reçoivent une première photographie de leur remplissage : leur courbe d'évolution part de l'état actuel."""
    Lot = apps.get_model('shipments', 'LotDedouanement')
    Colis = apps.get_model('shipments', 'Colis')
    Remplissage = apps.get_model('shipments', 'RemplissageLot')
    for lot in Lot.objects.all():
        colis = Colis.objects.filter(lot=lot)
        volume = colis.aggregate(v=Sum(Coalesce('volume_m3', 'volume_declare_m3')))['v'] or Decimal('0')
        pct = (Decimal(volume) / lot.capacite_m3 * 100) if lot.capacite_m3 else Decimal('0')
        Remplissage.objects.create(lot=lot, volume_m3=volume, pourcentage=pct.quantize(Decimal('0.01')), nb_colis=colis.count())


class Migration(migrations.Migration):
    dependencies = [('shipments', '0008_alter_lotdedouanement_options_and_more')]
    operations = [migrations.RunPython(photographier, migrations.RunPython.noop)]
