from .models import JournalActivite

def log_action(auteur, action, cible_description, **details):
    JournalActivite.objects.create(
        auteur=auteur, action=action, cible_description=cible_description, details=details,
    )