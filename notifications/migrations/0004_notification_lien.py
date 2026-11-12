from django.db import migrations, models


def remplir_liens(apps, schema_editor):
    """Notifications existantes : le lien est recalculé selon l'espace du destinataire (client, agent, administrateur)."""
    Notification = apps.get_model('notifications', 'Notification')
    Administrateur = apps.get_model('users', 'Administrateur')
    Agent = apps.get_model('users', 'Agent')
    admins = set(Administrateur.objects.values_list('pk', flat=True))
    agents = set(Agent.objects.values_list('pk', flat=True))
    for n in Notification.objects.filter(lien='').only('id', 'utilisateur_id', 'colis_id').iterator():
        espace = 'admin' if n.utilisateur_id in admins else 'agent' if n.utilisateur_id in agents else 'client'
        if n.colis_id:
            lien = {'client': f"/shipments/{n.colis_id}", 'agent': f"/agent/shipments/{n.colis_id}", 'admin': f"/admin/colis/{n.colis_id}"}[espace]
        else:
            lien = {'client': '/notifications', 'agent': '/agent/notifications', 'admin': '/admin/notifications'}[espace]
        Notification.objects.filter(pk=n.pk).update(lien=lien)


class Migration(migrations.Migration):

    dependencies = [
        ('notifications', '0003_fcmtoken'),
        ('users', '0006_client_consentement'),
    ]

    operations = [
        migrations.AddField(
            model_name='notification',
            name='lien',
            field=models.CharField(blank=True, default='', max_length=255),
        ),
        migrations.RunPython(remplir_liens, migrations.RunPython.noop),
    ]
