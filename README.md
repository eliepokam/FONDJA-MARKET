# Fondja Market — API Backend

Plateforme de gestion et suivi de colis Chine → Cameroun. API REST construite avec Django, Django REST Framework et PostgreSQL.

## Prérequis

- Python 3.11+
- PostgreSQL 14+

## Installation

\`\`\`bash
git clone <url-du-repo>
cd fondja-market
python -m venv venv
venv\Scripts\Activate.ps1        # Windows PowerShell
pip install -r requirements.txt
\`\`\`

Copie `.env.example` en `.env` et renseigne tes propres valeurs (base de données, clé secrète) :
\`\`\`bash
cp .env.example .env
\`\`\`

Crée la base PostgreSQL, puis :
\`\`\`bash
python manage.py migrate
python manage.py createsuperuser
python manage.py runserver
\`\`\`

## Documentation de l'API

Une fois le serveur lancé : `http://127.0.0.1:8000/api/docs/`

## Lancer les tests

\`\`\`bash
python manage.py test
\`\`\`

## Structure des apps

| App | Responsabilité |
|---|---|
| `users` | Authentification (JWT + OTP SMS/WhatsApp), rôles (Client/Administrateur/Agent) |
| `shipments` | Colis, lots de dédouanement, tarifs |
| `tracking` | Historique des statuts, photos des colis |
| `payments` | Paiements Mobile Money (simulés pour ce sprint) |
| `complaints` | Réclamations et messages |
| `notifications` | Notifications aux clients |
| `assistant` | Conversations avec l'assistant virtuel (structure posée, logique IA non implémentée) |

## Hors périmètre de ce sprint

Tâches asynchrones (Celery/Redis), intégration réelle des prestataires SMS/WhatsApp/Mobile Money, stockage S3, assistant virtuel (logique IA), déploiement.