"""Virement bancaire : comptes, preuve, vérification par un agent ou un administrateur, suppression du paiement à la livraison, chiffre d'affaires."""
import io
import tempfile
from datetime import datetime
from decimal import Decimal
from zoneinfo import ZoneInfo

from django.core.cache import cache
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import override_settings
from django.utils import timezone
from PIL import Image
from rest_framework.test import APITestCase, APIClient

from core.models import JournalActivite
from notifications.models import Notification
from payments.models import CompteBancaire, Paiement
from shipments.models import Colis
from users.models import Administrateur, Agent, Client

DOUALA = ZoneInfo('Africa/Douala')


def png(nom='recu.png'):
    t = io.BytesIO(); Image.new('RGB', (6, 6), 'white').save(t, format='PNG')
    return SimpleUploadedFile(nom, t.getvalue(), content_type='image/png')


def pdf(nom='recu.pdf'):
    return SimpleUploadedFile(nom, b'%PDF-1.4\n%fake but starts like a pdf\n', content_type='application/pdf')


def api(user=None):
    a = APIClient()
    if user: a.force_authenticate(user=user)
    return a


class Base(APITestCase):
    def setUp(self):
        cache.clear()
        self.dossier = tempfile.TemporaryDirectory(); self.addCleanup(self.dossier.cleanup)
        r = override_settings(PRIVATE_MEDIA_ROOT=self.dossier.name); r.enable(); self.addCleanup(r.disable)
        self.alice = Client.objects.create(nom='Alice Ngo', telephone='+237677991001', code_client='CL-VR01', telephone_verifie_le=timezone.now())
        self.bruno = Client.objects.create(nom='Bruno', telephone='+237677991002', code_client='CL-VR02', telephone_verifie_le=timezone.now())
        self.admin = Administrateur.objects.create(nom='Directrice', telephone='+237677991003')
        self.agent = Agent.objects.create(nom='Michael Agent', telephone='+237677991004', localisation='cameroun', cree_par=self.admin)
        self.colis = Colis.objects.create(client=self.alice, code_suivi='FM-VR000001', mode_transport='maritime', cout_final=Decimal('45000'))
        self.compte = CompteBancaire.objects.create(banque='Afriland First Bank', titulaire='FONDJA MARKET SARL', numero_compte='10005 00001 12345678901 23',
                                                    instructions='Indiquez le code du colis en motif.')

    def creer(self, user=None, **kw):
        corps = {'colis': self.colis.pk, 'methode': 'virement', 'moment': 'hors_ligne'}; corps.update(kw)
        return api(user or self.alice).post('/api/paiements/', corps, format='json')

    def virement(self, avec_preuve=True, colis=None, client=None):
        p = Paiement.objects.create(client=client or self.alice, colis=colis or self.colis, montant=(colis or self.colis).cout_final, methode='virement', moment='hors_ligne')
        if avec_preuve:
            r = api(p.client).post(f'/api/paiements/{p.pk}/preuve/', {'preuve': png()}, format='multipart')
            assert r.status_code == 200, r.content
            p.refresh_from_db()
        return p


class CreationEtSuppressionDeLaLivraisonTests(Base):
    def test_creation_d_un_virement(self):
        r = self.creer()
        self.assertEqual(r.status_code, 201, r.data)
        self.assertEqual((r.data['methode'], r.data['moment'], r.data['statut'], r.data['etape_virement']), ('virement', 'hors_ligne', 'en_attente', 'preuve_a_fournir'))
        self.assertEqual(Decimal(r.data['montant']), Decimal('45000'))                    # montant calculé par le serveur
        self.assertIsNone(r.data['telephone_paiement'])
        self.assertIsNone(r.data['preuve_url'])

    def test_le_montant_n_est_jamais_choisi_par_le_client(self):
        r = self.creer(montant='1')
        self.assertEqual(Decimal(r.data['montant']), Decimal('45000'))

    def test_virement_impossible_sans_compte_bancaire_actif(self):
        CompteBancaire.objects.update(actif=False)
        r = self.creer()
        self.assertEqual(r.status_code, 400)
        self.assertIn("aucun compte bancaire", r.data['methode'][0])

    def test_le_paiement_a_la_livraison_est_refuse(self):
        for corps in ({'methode': 'especes', 'moment': 'a_la_livraison'}, {'methode': 'especes', 'moment': 'hors_ligne'},
                      {'methode': 'orange_money', 'moment': 'a_la_livraison', 'telephone_paiement': '690123456'}):
            r = self.creer(**corps)
            self.assertEqual(r.status_code, 400, corps)
            self.assertIn("n'est plus proposé", r.data['methode'][0])
        self.assertEqual(Paiement.objects.count(), 0)

    def test_moment_incoherent(self):
        self.assertEqual(self.creer(moment='en_ligne').status_code, 400)                                  # virement « en ligne »
        self.assertEqual(self.creer(methode='mtn_momo', moment='hors_ligne', telephone_paiement='677123456').status_code, 400)

    def test_un_seul_paiement_actif_par_colis(self):
        self.assertEqual(self.creer().status_code, 201)
        r = self.creer()
        self.assertEqual(r.status_code, 400)

    def test_historique_conserve_les_anciens_paiements_especes(self):
        ancien = Paiement.objects.create(client=self.alice, colis=self.colis, montant='1000', methode='especes', moment='a_la_livraison', statut='paye', paye_le=timezone.now())
        self.assertEqual(api(self.alice).get(f'/api/paiements/{ancien.pk}/').status_code, 200)

    def test_comptes_bancaires_visibles_du_client_seulement_actifs(self):
        CompteBancaire.objects.create(banque='Ancienne banque', titulaire='X', numero_compte='1', actif=False)
        r = api(self.alice).get('/api/comptes-bancaires/')
        self.assertEqual([c['banque'] for c in r.json()], ['Afriland First Bank'])
        self.assertEqual(r.json()[0]['numero_compte'], '10005 00001 12345678901 23')
        self.assertEqual(api().get('/api/comptes-bancaires/').status_code, 401)


class PreuveTests(Base):
    def envoyer(self, user, p, fichier, **extra):
        return api(user).post(f'/api/paiements/{p.pk}/preuve/', {'preuve': fichier, **extra}, format='multipart')

    def test_le_client_envoie_sa_preuve_photo_ou_pdf(self):
        for fichier, ext in ((png(), 'png'), (pdf(), 'pdf')):
            p = Paiement.objects.create(client=self.alice, colis=self.colis, montant='45000', methode='virement', moment='hors_ligne')
            r = self.envoyer(self.alice, p, fichier, reference_virement='  VIR-2026-777  ')
            self.assertEqual(r.status_code, 200, r.content)
            self.assertEqual((r.json()['etape_virement'], r.json()['reference_virement']), ('en_verification', 'VIR-2026-777'))
            p.refresh_from_db()
            self.assertTrue(p.preuve.name.endswith(f'.{ext}'))
            self.assertNotIn('recu', p.preuve.name)                                   # nom d'origine jamais conservé
            p.statut = 'echoue'; p.save()

    def test_la_preuve_n_est_pas_dans_le_dossier_public(self):
        from django.conf import settings
        p = self.virement()
        self.assertTrue(p.preuve.path.startswith(self.dossier.name))
        self.assertFalse(p.preuve.path.startswith(str(settings.MEDIA_ROOT)))
        self.assertTrue(p.preuve.path.startswith(self.dossier.name))

    def test_fichiers_refuses(self):
        p = Paiement.objects.create(client=self.alice, colis=self.colis, montant='45000', methode='virement', moment='hors_ligne')
        for nom, contenu in (('virus.exe', b'MZ\x90\x00 not an image'), ('texte.txt', b'bonjour'), ('faux.png', b'pas une image du tout')):
            r = self.envoyer(self.alice, p, SimpleUploadedFile(nom, contenu))
            self.assertEqual(r.status_code, 400, nom)
            self.assertIn('photo', r.json()['preuve'][0])
        gros = SimpleUploadedFile('gros.pdf', b'%PDF-1.4\n' + b'0' * (8 * 1024 * 1024 + 1))
        self.assertEqual(self.envoyer(self.alice, p, gros).json()['preuve'], ['Fichier trop lourd (8 Mo maximum).'])
        gif = io.BytesIO(); Image.new('RGB', (4, 4)).save(gif, format='GIF')
        self.assertEqual(self.envoyer(self.alice, p, SimpleUploadedFile('a.gif', gif.getvalue())).status_code, 400)
        p.refresh_from_db(); self.assertFalse(p.preuve)

    def test_preuve_obligatoire(self):
        p = Paiement.objects.create(client=self.alice, colis=self.colis, montant='45000', methode='virement', moment='hors_ligne')
        r = api(self.alice).post(f'/api/paiements/{p.pk}/preuve/', {}, format='multipart')
        self.assertEqual(r.status_code, 400)

    def test_remplacer_la_preuve_supprime_l_ancienne(self):
        import os
        p = self.virement(); ancien = p.preuve.path
        self.assertEqual(self.envoyer(self.alice, p, pdf()).status_code, 200)
        p.refresh_from_db()
        self.assertNotEqual(p.preuve.path, ancien)
        self.assertFalse(os.path.exists(ancien))

    def test_seul_le_proprietaire_peut_envoyer_une_preuve(self):
        p = Paiement.objects.create(client=self.alice, colis=self.colis, montant='45000', methode='virement', moment='hors_ligne')
        self.assertEqual(self.envoyer(self.bruno, p, png()).status_code, 404)
        for staff in (self.agent, self.admin):
            self.assertEqual(self.envoyer(staff, p, png()).status_code, 403)
        self.assertEqual(self.envoyer(None, p, png()).status_code, 401)

    def test_pas_de_preuve_pour_le_mobile_money_ni_apres_traitement(self):
        mm = Paiement.objects.create(client=self.alice, colis=self.colis, montant='45000', methode='mtn_momo', moment='en_ligne', statut='echoue')
        self.assertEqual(self.envoyer(self.alice, mm, png()).status_code, 400)
        p = self.virement(); api(self.admin).post(f'/api/paiements/{p.pk}/valider/')
        r = self.envoyer(self.alice, p, png())
        self.assertEqual(r.status_code, 400); self.assertIn('déjà été traité', r.json()['detail'])

    def test_le_personnel_est_prevenu(self):
        self.virement()
        textes = list(Notification.objects.filter(contenu__contains='Preuve de virement à vérifier').values_list('utilisateur_id', flat=True))
        self.assertEqual(set(textes), {self.agent.pk, self.admin.pk})
        self.assertIn('45 000 XAF', Notification.objects.filter(utilisateur=self.agent).first().contenu)

    def test_telechargement_de_la_preuve_reserve_aux_personnes_autorisees(self):
        p = self.virement()
        url = f'/api/paiements/{p.pk}/preuve-fichier/'
        for ok in (self.alice, self.agent, self.admin):
            r = api(ok).get(url)
            self.assertEqual(r.status_code, 200, ok)
            self.assertEqual(r['Content-Type'], 'image/png')
            self.assertEqual(r['X-Content-Type-Options'], 'nosniff')
            self.assertEqual(r['Cache-Control'], 'private, no-store')
            self.assertTrue(b''.join(r.streaming_content).startswith(b'\x89PNG'))
        self.assertEqual(api(self.bruno).get(url).status_code, 404)
        self.assertEqual(api().get(url).status_code, 401)

    def test_aucune_adresse_publique_de_la_preuve(self):
        r = api(self.alice).get(f'/api/paiements/{self.virement().pk}/')
        self.assertTrue(r.json()['preuve_url'].endswith('/preuve-fichier/'))
        self.assertNotIn('/media/', r.json()['preuve_url'])
        self.assertNotIn('verifie_par', r.json())


class VerificationTests(Base):
    def test_un_agent_valide_et_le_paiement_est_accepte(self):
        p = self.virement()
        r = api(self.agent).post(f'/api/paiements/{p.pk}/valider/')
        self.assertEqual(r.status_code, 200, r.content)
        p.refresh_from_db()
        self.assertEqual((p.statut, p.verifie_par_id), ('paye', self.agent.pk))
        self.assertIsNotNone(p.paye_le); self.assertEqual(p.reference_prestataire, f'VIREMENT-{p.pk:06d}')
        n = Notification.objects.filter(utilisateur=self.alice).latest('id')
        self.assertIn('a été validé', n.contenu)
        self.assertFalse(JournalActivite.objects.filter(action='validation_virement').exists())      # le journal est celui des administrateurs

    def test_un_administrateur_valide_et_c_est_journalise(self):
        p = self.virement()
        self.assertEqual(api(self.admin).post(f'/api/paiements/{p.pk}/valider/').status_code, 200)
        j = JournalActivite.objects.get(action='validation_virement')
        self.assertEqual((j.auteur_id, j.cible_description, j.details['paiement']), (self.admin.pk, 'FM-VR000001', p.pk))

    def test_impossible_de_valider_sans_preuve(self):
        p = self.virement(avec_preuve=False)
        r = api(self.agent).post(f'/api/paiements/{p.pk}/valider/')
        self.assertEqual(r.status_code, 400)
        self.assertIn("pas encore envoyé de preuve", r.json()['detail'])
        p.refresh_from_db(); self.assertEqual(p.statut, 'en_attente')

    def test_validation_une_seule_fois(self):
        p = self.virement()
        self.assertEqual(api(self.agent).post(f'/api/paiements/{p.pk}/valider/').status_code, 200)
        r = api(self.admin).post(f'/api/paiements/{p.pk}/valider/')
        self.assertEqual(r.status_code, 409)
        self.assertEqual(api(self.admin).post(f'/api/paiements/{p.pk}/refuser/', {'motif': 'x'}, format='json').status_code, 409)

    def test_le_client_ne_valide_pas_son_propre_virement(self):
        p = self.virement()
        for action in ('valider', 'refuser'):
            self.assertEqual(api(self.alice).post(f'/api/paiements/{p.pk}/{action}/', {'motif': 'x'}, format='json').status_code, 403)
        self.assertEqual(api().post(f'/api/paiements/{p.pk}/valider/').status_code, 401)
        p.refresh_from_db(); self.assertEqual(p.statut, 'en_attente')

    def test_seuls_les_virements_se_verifient(self):
        mm = Paiement.objects.create(client=self.alice, colis=self.colis, montant='45000', methode='mtn_momo', moment='en_ligne')
        self.assertEqual(api(self.agent).post(f'/api/paiements/{mm.pk}/valider/').status_code, 400)

    def test_refus_avec_motif_le_client_peut_recommencer(self):
        p = self.virement()
        self.assertEqual(api(self.agent).post(f'/api/paiements/{p.pk}/refuser/', {}, format='json').status_code, 400)       # motif obligatoire
        self.assertEqual(api(self.agent).post(f'/api/paiements/{p.pk}/refuser/', {'motif': '   '}, format='json').status_code, 400)
        r = api(self.agent).post(f'/api/paiements/{p.pk}/refuser/', {'motif': 'Montant illisible sur le reçu.'}, format='json')
        self.assertEqual(r.status_code, 200, r.content)
        self.assertEqual((r.json()['statut'], r.json()['etape_virement'], r.json()['motif_refus']), ('echoue', 'refuse', 'Montant illisible sur le reçu.'))
        self.assertIn('Montant illisible', Notification.objects.filter(utilisateur=self.alice).latest('id').contenu)
        self.assertEqual(self.creer().status_code, 201)                                  # un échoué ne bloque plus le colis
        self.assertFalse(Paiement.objects.filter(statut='paye').exists())

    def test_refus_journalise_pour_un_administrateur(self):
        p = self.virement()
        api(self.admin).post(f'/api/paiements/{p.pk}/refuser/', {'motif': 'Doublon'}, format='json')
        self.assertEqual(JournalActivite.objects.get(action='refus_virement').details['motif'], 'Doublon')


class FileDeVerificationTests(Base):
    def setUp(self):
        super().setUp()
        self.colis2 = Colis.objects.create(client=self.bruno, code_suivi='FM-VR000002', mode_transport='maritime', cout_final=Decimal('12000'))
        self.colis3 = Colis.objects.create(client=self.bruno, code_suivi='FM-VR000003', mode_transport='maritime', cout_final=Decimal('7000'))
        self.a_payer = self.virement(colis=self.colis)
        self.valide = self.virement(colis=self.colis2, client=self.bruno)
        api(self.agent).post(f'/api/paiements/{self.valide.pk}/valider/')
        self.sans_preuve = self.virement(avec_preuve=False, colis=self.colis3, client=self.bruno)

    def liste(self, user, etat=None):
        r = api(user).get('/api/virements/' + (f'?etat={etat}' if etat else ''))
        self.assertEqual(r.status_code, 200, r.content)
        return r.json()['results']

    def test_par_defaut_les_virements_a_verifier(self):
        for staff in (self.agent, self.admin):
            res = self.liste(staff)
            self.assertEqual([x['id'] for x in res], [self.a_payer.pk])                  # ni le validé, ni celui sans preuve
            x = res[0]
            self.assertEqual((x['code_suivi'], x['client_nom'], x['montant'], x['etape']), ('FM-VR000001', 'Alice Ngo', '45000.00', 'en_verification'))
            self.assertTrue(x['preuve_url'].endswith(f'/api/paiements/{self.a_payer.pk}/preuve-fichier/'))

    def test_onglets_valides_refuses_tous(self):
        refuse = self.virement(colis=self.colis3, client=self.bruno)
        api(self.admin).post(f'/api/paiements/{refuse.pk}/refuser/', {'motif': 'Flou'}, format='json')
        self.assertEqual([x['id'] for x in self.liste(self.admin, 'valides')], [self.valide.pk])
        self.assertEqual(self.liste(self.admin, 'valides')[0]['verifie_par_nom'], 'Michael Agent')
        self.assertEqual([x['id'] for x in self.liste(self.admin, 'refuses')], [refuse.pk])
        self.assertEqual(len(self.liste(self.admin, 'tous')), 4)

    def test_le_plus_ancien_d_abord(self):
        c4 = Colis.objects.create(client=self.alice, code_suivi='FM-VR000004', mode_transport='maritime', cout_final=Decimal('3000'))
        plus_recent = self.virement(colis=c4)
        Paiement.objects.filter(pk=self.a_payer.pk).update(preuve_envoyee_le=timezone.now() + timezone.timedelta(hours=2))
        self.assertEqual([x['id'] for x in self.liste(self.agent)], [plus_recent.pk, self.a_payer.pk])

    def test_reserve_au_personnel(self):
        self.assertEqual(api(self.alice).get('/api/virements/').status_code, 403)
        self.assertEqual(api().get('/api/virements/').status_code, 401)

    def test_un_agent_ne_voit_toujours_aucun_autre_paiement(self):
        self.assertEqual(api(self.agent).get('/api/paiements/').status_code, 403)

    def test_compteur_du_tableau_de_bord(self):
        for staff in (self.agent, self.admin):
            self.assertEqual(api(staff).get('/api/admin/stats/').json()['virements_a_verifier'], 1)


class ComptesBancairesAdminTests(Base):
    def test_cycle_complet_et_journal(self):
        a = api(self.admin)
        r = a.post('/api/admin/comptes-bancaires/', {'banque': 'UBA', 'titulaire': 'FONDJA MARKET', 'numero_compte': '123', 'iban': 'CM21 1000 1000', 'instructions': 'Motif = code colis'}, format='json')
        self.assertEqual(r.status_code, 201, r.content)
        cid = r.json()['id']
        self.assertEqual(a.patch(f'/api/admin/comptes-bancaires/{cid}/', {'actif': False}, format='json').status_code, 200)
        self.assertEqual(len(a.get('/api/admin/comptes-bancaires/').json()), 2)                         # l'admin voit aussi les inactifs
        self.assertEqual([c['banque'] for c in api(self.alice).get('/api/comptes-bancaires/').json()], ['Afriland First Bank'])
        self.assertEqual(a.delete(f'/api/admin/comptes-bancaires/{cid}/').status_code, 204)
        ops = [j.details['operation'] for j in JournalActivite.objects.filter(action='modification_compte_bancaire').order_by('id')]
        self.assertEqual(ops, ['création', 'modification', 'suppression'])
        self.assertNotIn('123', str(list(JournalActivite.objects.values_list('details', flat=True))))   # le numéro de compte n'est pas recopié au journal

    def test_champs_obligatoires(self):
        r = api(self.admin).post('/api/admin/comptes-bancaires/', {'banque': ' ', 'titulaire': 'X', 'numero_compte': '1'}, format='json')
        self.assertEqual(r.status_code, 400)

    def test_reserve_a_l_administrateur(self):
        for user in (self.alice, self.agent):
            self.assertEqual(api(user).get('/api/admin/comptes-bancaires/').status_code, 403)
            self.assertEqual(api(user).post('/api/admin/comptes-bancaires/', {'banque': 'B', 'titulaire': 'T', 'numero_compte': '1'}, format='json').status_code, 403)
        self.assertEqual(api().get('/api/admin/comptes-bancaires/').status_code, 401)


class ChiffreAffairesTests(Base):
    def payer(self, montant, quand, methode='mtn_momo', statut='paye'):
        c = Colis.objects.create(client=self.alice, code_suivi=f'FM-CA{Colis.objects.count():06d}', mode_transport='maritime', cout_final=Decimal(montant))
        return Paiement.objects.create(client=self.alice, colis=c, montant=montant, methode=methode, moment={'virement': 'hors_ligne', 'especes': 'a_la_livraison'}.get(methode, 'en_ligne'),
                                       statut=statut, paye_le=quand if statut == 'paye' else None)

    def ca(self, user=None, **params):
        r = api(user or self.admin).get('/api/admin/chiffre-affaires/', params)
        self.assertEqual(r.status_code, 200, r.content)
        return r.json()

    def donnees(self):
        d = lambda *a: datetime(*a, tzinfo=DOUALA)
        self.payer(10000, d(2026, 11, 3, 9)); self.payer(5000, d(2026, 11, 3, 15), 'virement'); self.payer(20000, d(2026, 11, 30, 23, 30), 'orange_money')
        self.payer(7000, d(2026, 10, 31, 23, 59)); self.payer(40000, d(2026, 3, 10)); self.payer(30000, d(2025, 12, 31, 12), 'virement')
        self.payer(99999, d(2026, 11, 5), statut='en_attente'); self.payer(88888, None, statut='echoue')
        self.payer(1000, d(2026, 11, 8), 'especes', 'paye')

    def test_mensuel(self):
        self.donnees()
        r = self.ca(periode='mois', annee=2026, mois=11)
        self.assertEqual((r['total'], r['nb_paiements'], r['libelle']), (36000.0, 4, 'novembre 2026'))
        self.assertEqual(r['precedent'], {'libelle': 'octobre 2026', 'total': 7000.0})
        self.assertEqual(r['variation_pct'], round((36000 - 7000) / 7000 * 100, 1))
        par = {m['methode']: (m['total'], m['nb']) for m in r['par_methode']}
        self.assertEqual(par, {'orange_money': (20000.0, 1), 'mtn_momo': (10000.0, 1), 'virement': (5000.0, 1), 'especes': (1000.0, 1)})
        self.assertEqual([m['libelle'] for m in r['par_methode']][0], 'Orange Money')
        self.assertEqual(len(r['serie']), 30)
        jours = {s['libelle']: s['total'] for s in r['serie']}
        self.assertEqual((jours['3'], jours['30'], jours['1'], jours['8']), (15000.0, 20000.0, 0.0, 1000.0))     # 30 nov. 23 h 30 reste en novembre (heure du Cameroun)

    def test_les_limites_de_mois_suivent_l_heure_du_cameroun(self):
        self.donnees()
        self.assertEqual(self.ca(periode='mois', annee=2026, mois=10)['total'], 7000.0)            # 31 oct. 23 h 59 reste en octobre
        self.assertEqual(self.ca(periode='mois', annee=2025, mois=12)['total'], 30000.0)
        self.assertEqual(self.ca(periode='mois', annee=2026, mois=1)['precedent']['libelle'], 'décembre 2025')

    def test_annuel(self):
        self.donnees()
        r = self.ca(periode='annee', annee=2026)
        self.assertEqual((r['total'], r['nb_paiements']), (83000.0, 6))
        self.assertEqual(len(r['serie']), 12)
        mois = {s['cle']: s['total'] for s in r['serie']}
        self.assertEqual((mois['2026-11'], mois['2026-10'], mois['2026-03'], mois['2026-01']), (36000.0, 7000.0, 40000.0, 0.0))
        self.assertEqual(r['precedent'], {'libelle': '2025', 'total': 30000.0})
        self.assertEqual(r['variation_pct'], round((83000 - 30000) / 30000 * 100, 1))

    def test_global(self):
        self.donnees()
        r = self.ca(periode='global')
        self.assertEqual((r['total'], r['nb_paiements'], r['precedent'], r['variation_pct']), (113000.0, 7, None, None))
        self.assertEqual([(s['libelle'], s['total']) for s in r['serie']], [('2025', 30000.0), ('2026', 83000.0)])
        self.assertEqual(r['premiere_annee'], 2025)

    def test_seuls_les_paiements_encaisses_comptent(self):
        self.payer(5000, timezone.now(), 'virement', 'en_attente'); self.payer(6000, None, 'mtn_momo', 'echoue')
        self.assertEqual(self.ca(periode='global')['total'], 0.0)

    def test_un_virement_compte_a_la_validation_pas_a_la_declaration(self):
        p = self.virement()
        avant = self.ca(periode='global')
        self.assertEqual(avant['total'], 0.0)
        api(self.agent).post(f'/api/paiements/{p.pk}/valider/')
        apres = self.ca(periode='global')
        self.assertEqual(apres['total'], 45000.0)
        self.assertEqual(apres['par_methode'][0]['methode'], 'virement')
        self.assertEqual(self.ca(periode='mois')['total'], 45000.0)                              # mois en cours par défaut
        self.assertEqual(api(self.admin).get('/api/admin/stats/').json()['ca_du_mois'], 45000.0)  # l'ancien indicateur en profite aussi

    def test_defauts_et_validations(self):
        self.assertEqual(self.ca()['periode'], 'mois')
        for params in ({'periode': 'semaine'}, {'periode': 'annee', 'annee': 'abc'}, {'periode': 'mois', 'mois': 13}, {'periode': 'annee', 'annee': 1900}):
            self.assertEqual(api(self.admin).get('/api/admin/chiffre-affaires/', params).status_code, 400, params)

    def test_reserve_a_l_administrateur(self):
        for user in (self.alice, self.agent):
            self.assertEqual(api(user).get('/api/admin/chiffre-affaires/').status_code, 403)
        self.assertEqual(api().get('/api/admin/chiffre-affaires/').status_code, 401)


class MigrationDeLiberationTests(Base):
    def test_les_anciens_paiements_a_la_livraison_en_attente_sont_liberes_les_encaisses_restent(self):
        import importlib
        from django.apps import apps
        migration = importlib.import_module('payments.migrations.0007_liberer_paiements_a_la_livraison')
        c2 = Colis.objects.create(client=self.alice, code_suivi='FM-VR000009', mode_transport='maritime', cout_final=Decimal('1000'))
        attente = Paiement.objects.create(client=self.alice, colis=self.colis, montant='45000', methode='especes', moment='a_la_livraison', statut='en_attente')
        encaisse = Paiement.objects.create(client=self.alice, colis=c2, montant='1000', methode='especes', moment='a_la_livraison', statut='paye', paye_le=timezone.now())
        autre = Paiement.objects.create(client=self.bruno, colis=Colis.objects.create(client=self.bruno, code_suivi='FM-VR000010', mode_transport='maritime', cout_final=Decimal('5')),
                                        montant='5', methode='mtn_momo', moment='en_ligne', statut='en_attente')
        migration.liberer(apps, None)
        for p in (attente, encaisse, autre):
            p.refresh_from_db()
        self.assertEqual((attente.statut, encaisse.statut, autre.statut), ('echoue', 'paye', 'en_attente'))
        self.assertEqual(self.creer().status_code, 201)                              # le colis est de nouveau payable
