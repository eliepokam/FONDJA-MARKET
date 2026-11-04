"""
Consentement aux conditions d'utilisation et à la politique de confidentialité.

La loi camerounaise n° 2024/017 interdit le traitement de données personnelles sans consentement préalable. On enregistre donc,
pour chaque client, QUAND il a accepté et QUELLE version des textes. Quand les textes changent de façon importante, il suffit
d'augmenter CONSENT_VERSION (variable d'environnement) : chaque client est invité à accepter à nouveau à sa prochaine ouverture.
"""
from django.conf import settings

VERSION_PAR_DEFAUT = "2026-11-03"   # = date de dernière mise à jour affichée sur les pages légales


def version_courante():
    return getattr(settings, 'CONSENT_VERSION', VERSION_PAR_DEFAUT)
