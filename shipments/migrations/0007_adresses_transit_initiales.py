from django.db import migrations

# Adresses fournies par FONDJA MARKET. get_or_create : si un administrateur les a déjà modifiées, elles ne sont JAMAIS écrasées.
ADRESSES = [
    {
        'mode': 'aerien',
        'titre': 'Entrepôt aérien — Guangzhou',
        'adresse': '广东省广州市白云区三元里大道北站五福服装城 3C020',
        'destinataire': 'Michael',
        'telephone': '135 4444 0536',
        'marquage': '',
        'instructions': "Pour les envois par AVION (express ou standard). Donnez cette adresse complète à votre fournisseur : "
                        "il doit expédier le colis directement à l'entrepôt de Guangzhou, au nom du destinataire indiqué.",
    },
    {
        'mode': 'maritime',
        'titre': 'Entrepôt maritime — Foshan',
        'adresse': '广东省佛山市南海区里水镇西线东路甘蔗工业区内138号之六',
        'destinataire': '广东爱德宝食品有限公司',
        'telephone': '',
        'marquage': 'Mary777',
        'instructions': "Pour les envois par BATEAU (maritime). Votre fournisseur doit écrire l'adresse, le nom de la société destinataire "
                        "ET le marquage « Mary777 » sur le colis : c'est ce marquage qui permet de reconnaître votre marchandise à l'arrivée.",
    },
]


def creer(apps, schema_editor):
    AdresseTransit = apps.get_model('shipments', 'AdresseTransit')
    for valeurs in ADRESSES:
        AdresseTransit.objects.get_or_create(mode=valeurs['mode'], defaults=valeurs)


class Migration(migrations.Migration):
    dependencies = [('shipments', '0006_adressetransit')]
    operations = [migrations.RunPython(creer, migrations.RunPython.noop)]
