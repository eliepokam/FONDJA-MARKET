import getpass
import sys

from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils import timezone
from phonenumber_field.phonenumber import PhoneNumber

from users.models import Administrateur, Utilisateur


class Command(BaseCommand):
    help = (
        "Crée un compte ADMINISTRATEUR (accès au portail /staff-portal/login et à l'espace d'administration). "
        "À utiliser pour le tout premier administrateur : ensuite, les agents et administrateurs se gèrent depuis l'application.\n"
        "Sans options, la commande pose les questions (le mot de passe n'est jamais affiché). "
        "Si le numéro est déjà administrateur, --reinitialiser change son mot de passe et le débloque."
    )

    def add_arguments(self, parser):
        parser.add_argument('--nom', help="Nom complet.")
        parser.add_argument('--telephone', help="Numéro de téléphone, ex. +237677123456 ou 677 12 34 56 (Cameroun par défaut).")
        parser.add_argument('--password', help="Mot de passe (déconseillé : il resterait dans l'historique du terminal ; laissez la commande le demander).")
        parser.add_argument('--reinitialiser', action='store_true', help="Si le numéro est déjà administrateur : définit un nouveau mot de passe et débloque le compte.")
        parser.add_argument('--sans-django-admin', action='store_true',
                            help="Ne donne pas l'accès à l'interface Django /admin/ (le compte reste administrateur de l'application).")

    # ─────────────────────────────────────────────────────────────────────────────────────────────
    def handle(self, *args, **options):
        interactif = sys.stdin.isatty()
        nom = (options['nom'] or (input("Nom complet : ") if interactif else '')).strip()
        brut = (options['telephone'] or (input("Téléphone (ex. 677 12 34 56) : ") if interactif else '')).strip()
        if not nom or not brut:
            raise CommandError("Le nom et le téléphone sont obligatoires (--nom et --telephone).")
        telephone = self.normaliser_telephone(brut)

        existant = Utilisateur.objects.filter(telephone=telephone).first()

        if existant is not None and hasattr(existant, 'administrateur'):
            return self.reinitialiser(existant, options, interactif)
        if existant is not None and (hasattr(existant, 'client') or hasattr(existant, 'agent')):
            role = 'client' if hasattr(existant, 'client') else 'agent'
            raise CommandError(f"Ce numéro appartient déjà à un compte {role} : un compte ne peut pas changer de rôle. Utilisez un autre numéro.")

        with transaction.atomic():
            if existant is not None:
                admin = self.promouvoir(existant, nom, options, interactif)
            else:
                password = self.obtenir_mot_de_passe(options, interactif)
                admin = Administrateur(nom=nom, telephone=telephone, telephone_verifie_le=timezone.now(),
                                       is_superuser=not options['sans_django_admin'])
                admin.set_password(password)
                admin.save()
        self.resume(admin, "créé")

    # ─────────────────────────────────────────────────────────────────────────────────────────────
    def normaliser_telephone(self, brut):
        nettoye = brut.replace(' ', '')
        if nettoye.startswith('00'):
            nettoye = '+' + nettoye[2:]
        numero = PhoneNumber.from_string(nettoye, region='CM')
        if not numero.is_valid():
            raise CommandError(f"Numéro de téléphone invalide : {brut!r}. Exemple : +237677123456.")
        return numero.as_e164

    def obtenir_mot_de_passe(self, options, interactif, utilisateur=None):
        if options['password']:
            password = options['password']
        elif interactif:
            password = getpass.getpass("Mot de passe : ")
            if password != getpass.getpass("Confirmez le mot de passe : "):
                raise CommandError("Les deux mots de passe ne correspondent pas. Rien n'a été créé.")
        else:
            raise CommandError("Mot de passe requis (--password), ou lancez la commande dans un terminal pour qu'elle le demande.")
        try:
            validate_password(password, user=utilisateur)
        except ValidationError as exc:
            raise CommandError("Mot de passe refusé : " + " ".join(exc.messages))
        return password

    def reinitialiser(self, existant, options, interactif):
        if not options['reinitialiser']:
            self.stdout.write(self.style.WARNING(
                "Ce numéro est déjà administrateur. Pour changer son mot de passe (et débloquer le compte) : ajoutez --reinitialiser."))
            return
        password = self.obtenir_mot_de_passe(options, interactif, existant)
        existant.set_password(password)
        existant.statut = Utilisateur.Statut.ACTIF
        existant.tentatives_connexion_echouees = 0
        existant.bloque_jusqu_a = None
        existant.save()
        self.resume(existant, "mis à jour (nouveau mot de passe, compte débloqué)")

    def promouvoir(self, existant, nom, options, interactif):
        """Compte « nu » (ex. créé avec createsuperuser) : on lui ajoute le rôle Administrateur, sans toucher à son mot de passe sauf demande."""
        admin = Administrateur(utilisateur_ptr=existant)
        admin.__dict__.update(existant.__dict__)
        admin.telephone_verifie_le = existant.telephone_verifie_le or timezone.now()
        admin.is_superuser = existant.is_superuser or not options['sans_django_admin']
        admin.statut = Utilisateur.Statut.ACTIF
        if options['password'] or options['reinitialiser']:
            admin.set_password(self.obtenir_mot_de_passe(options, interactif, existant))
        admin.save()
        return admin

    def resume(self, admin, action):
        admin.refresh_from_db()
        self.stdout.write(self.style.SUCCESS(f"\nAdministrateur {action} : {admin.nom} ({admin.telephone})"))
        self.stdout.write("  Connexion de l'application : https://<votre-domaine>/staff-portal/login   (téléphone + mot de passe)")
        self.stdout.write("  Interface Django (si activée) : https://<votre-domaine>/django-admin/" + ("" if admin.is_superuser else "   → NON activée pour ce compte"))
