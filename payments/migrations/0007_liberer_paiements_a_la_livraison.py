from django.db import migrations


def liberer(apps, schema_editor):
    """
    Le paiement à la livraison n'existe plus. Un ancien paiement de ce type resté « en attente » bloquerait son colis (un seul paiement actif par colis) :
    on le marque « échoué ». Les paiements à la livraison DÉJÀ ENCAISSÉS ne sont pas touchés : ils restent dans l'historique et le chiffre d'affaires.
    """
    Paiement = apps.get_model('payments', 'Paiement')
    Paiement.objects.filter(moment='a_la_livraison', statut='en_attente').update(statut='echoue')


class Migration(migrations.Migration):
    dependencies = [('payments', '0006_comptebancaire_paiement_motif_refus_paiement_preuve_and_more')]
    operations = [migrations.RunPython(liberer, migrations.RunPython.noop)]
