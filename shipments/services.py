from notifications.models import Notification
from notifications.services import send_push_notification

def update_shipment_status(colis, new_status: str):
    colis.status = new_status
    colis.save()

    Notification.objects.create(
        utilisateur=colis.client,
        message=f"Votre colis est maintenant : {new_status}",
        channel="push",
    )
    send_push_notification(
        colis.client,
        title="Mise à jour de votre colis",
        body=f"Statut : {new_status}",
        link=f"/shipments/{colis.id}",
    )