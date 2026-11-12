"""
Lancement des tâches d'arrière-plan (code OTP, notifications push) SANS jamais bloquer ni faire échouer la requête en cours.

  • TACHES_VIA_CELERY=False (défaut) : la tâche s'exécute dans un fil du serveur web, juste après la validation en base.
    Aucun Redis ni worker Celery n'est nécessaire.
  • TACHES_VIA_CELERY=True : la tâche est confiée à Celery (Redis + « celery -A config worker » doivent tourner). Si Redis est
    injoignable, on l'exécute quand même dans un fil plutôt que de la perdre.

Sous `manage.py test`, tout s'exécute immédiatement dans le fil courant : les tests voient le résultat sans attendre.
"""
import logging
import sys
import threading

from django.conf import settings
from django.db import close_old_connections, transaction

logger = logging.getLogger('django')

_EN_TEST = len(sys.argv) > 1 and sys.argv[1] == 'test'


def _executer(tache, args):
    """Exécute la tâche ici même (avec ses éventuelles nouvelles tentatives) ; aucune exception ne remonte."""
    try:
        tache.apply(args=args, throw=False)
    except Exception as exc:  # pragma: no cover - filet de sécurité
        logger.warning(f"Tâche {tache.name} en échec : {exc}")


def _confier_a_celery_ou_executer(tache, args):
    if getattr(settings, 'TACHES_VIA_CELERY', False):
        try:
            tache.apply_async(args=args, retry=False)
            return
        except Exception as exc:   # Redis arrêté ou injoignable (l'échec peut prendre ~20 s : jamais dans la requête)
            logger.warning(f"Celery indisponible ({exc}) : la tâche {tache.name} s'exécute directement.")
    _executer(tache, args)


def _lancer_maintenant(tache, args):
    """Toujours dans un fil séparé : même un Redis injoignable ne retarde plus la réponse envoyée au navigateur."""
    def cible():
        close_old_connections()
        try:
            _confier_a_celery_ou_executer(tache, args)
        finally:
            close_old_connections()   # le fil ouvre sa propre connexion à la base : on la referme
    threading.Thread(target=cible, name=f"tache-{tache.name}", daemon=True).start()


def lancer_tache(tache, *args):
    """Programme `tache(*args)` après la validation de la transaction en cours (immédiatement s'il n'y en a pas)."""
    if _EN_TEST:   # les tests tournent dans une transaction jamais validée : on exécute tout de suite
        _executer(tache, args)
        return
    transaction.on_commit(lambda: _lancer_maintenant(tache, args))
