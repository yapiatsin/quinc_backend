"""Données de référence (permissions, agences, créneaux, catégories,
zones de livraison e-commerce, moyens de paiement, barèmes de timbre).

Appelé automatiquement après `migrate` (signal post_migrate) et par la
migration 0045. Toutes les opérations sont idempotentes (`get_or_create`).
"""
from datetime import time
from decimal import Decimal

from django.db import transaction
from django.db.utils import OperationalError, ProgrammingError
from django.utils.text import slugify


TYPE_PERMISSIONS = ['Autres', 'Comptes', 'Dashboard', 'Interfaces', 'Liste', 'Stocks']

CUSTOM_PERMISSIONS = [{'categorie': 'Comptes', 'name': 'Active Compte', 'url': 'active_compte'},
 {'categorie': 'Comptes', 'name': 'Compte Client', 'url': 'compte_client'},
 {'categorie': 'Comptes', 'name': 'Compte staff', 'url': 'add_compte'},
 {'categorie': 'Comptes', 'name': 'Deactive Compte', 'url': 'deactive_compte'},
 {'categorie': 'Comptes', 'name': 'Del Compte', 'url': 'del_compte'},
 {'categorie': 'Comptes', 'name': 'Del Compte Client', 'url': 'del_compte_client'},
 {'categorie': 'Comptes', 'name': 'Delete Localite', 'url': 'delete_localite'},
 {'categorie': 'Comptes', 'name': 'Detail Compte Client', 'url': 'detail_compte_client'},
 {'categorie': 'Comptes', 'name': 'Fiche Client Pdf', 'url': 'fiche_client_pdf'},
 {'categorie': 'Comptes', 'name': 'Update Compte', 'url': 'update_compte'},
 {'categorie': 'Comptes', 'name': 'Update Compte Client', 'url': 'update_compte_client'},
 {'categorie': 'Comptes', 'name': 'Update Localite', 'url': 'update_localite'},
 {'categorie': 'Dashboard', 'name': 'Tbord', 'url': 'tbord'},
 {'categorie': 'Interfaces', 'name': 'Add Proforma', 'url': 'add_proforma'},
 {'categorie': 'Interfaces', 'name': 'Ajouter Agence', 'url': 'add_localite'},
 {'categorie': 'Interfaces', 'name': 'Ajouter lieu de livraison', 'url': 'zones_livraison'},
 {'categorie': 'Interfaces', 'name': 'Commandes en ligne', 'url': 'cmd_line'},
 {'categorie': 'Interfaces', 'name': 'Chat clients', 'url': 'ecom_chat_inbox'},
 {'categorie': 'Interfaces', 'name': 'Historique chat clients', 'url': 'ecom_chat_history'},
 {'categorie': 'Interfaces', 'name': 'Messages de contact', 'url': 'ecom_messages_contact'},
 {'categorie': 'Interfaces', 'name': 'Livraison commande client', 'url': 'livraison_cmd_online'},
 {'categorie': 'Interfaces', 'name': 'Gestion des paramètres', 'url': 'gestion_parametre'},
 {'categorie': 'Interfaces', 'name': 'Service accueil', 'url': 'paniers'},
 {'categorie': 'Interfaces', 'name': 'Service caissiere', 'url': 'caissiere'},
 {'categorie': 'Interfaces', 'name': 'Service livraisons', 'url': 'livraisons'},
 {'categorie': 'Interfaces', 'name': 'Transferts inter-localités', 'url': 'liste_transferts'},
 {'categorie': 'Interfaces', 'name': 'Nouveau Fournisseur', 'url': 'nouveau_fournisseur'},
 {'categorie': 'Liste', 'name': 'Export Comptes Clients Excel', 'url': 'export_comptes_clients_excel'},
 {'categorie': 'Liste', 'name': 'Export Comptes Excel', 'url': 'export_comptes_excel'},
 {'categorie': 'Liste', 'name': 'Export Best Vente Excel', 'url': 'export_best_vente_excel'},
 {'categorie': 'Liste', 'name': 'Export Best Vente Pdf', 'url': 'export_best_vente_pdf'},
 {'categorie': 'Liste', 'name': 'Export Caisse Ventes Excel', 'url': 'export_caisse_ventes_excel'},
 {'categorie': 'Liste', 'name': 'Export Caisse Ventes Pdf', 'url': 'export_caisse_ventes_pdf'},
 {'categorie': 'Liste', 'name': 'Export Cmd Line Excel', 'url': 'export_cmd_line_excel'},
 {'categorie': 'Liste', 'name': 'Export Cmd Line Pdf', 'url': 'export_cmd_line_pdf'},
 {'categorie': 'Liste', 'name': 'Export Entrees Piece Excel', 'url': 'export_entrees_piece_excel'},
 {'categorie': 'Liste', 'name': 'Export Entrees Piece Pdf', 'url': 'export_entrees_piece_pdf'},
 {'categorie': 'Liste', 'name': 'Export Hist Cmd Excel', 'url': 'export_hist_cmd_excel'},
 {'categorie': 'Liste', 'name': 'Export Hist Cmd Pdf', 'url': 'export_hist_cmd_pdf'},
 {'categorie': 'Liste', 'name': 'Export Hist Gen Excel', 'url': 'export_hist_gen_excel'},
 {'categorie': 'Liste', 'name': 'Export Hist Gen Pdf', 'url': 'export_hist_gen_pdf'},
 {'categorie': 'Liste', 'name': 'Export Liste Commandes Excel', 'url': 'export_liste_commandes_excel'},
 {'categorie': 'Liste', 'name': 'Export Liste Commandes Pdf', 'url': 'export_liste_commandes_pdf'},
 {'categorie': 'Liste', 'name': 'Export Liste Ventes Excel', 'url': 'export_liste_ventes_excel'},
 {'categorie': 'Liste', 'name': 'Export Liste Ventes Pdf', 'url': 'export_liste_ventes_pdf'},
 {'categorie': 'Liste', 'name': 'Export Livraison Cmd Online Excel', 'url': 'export_livraison_cmd_online_excel'},
 {'categorie': 'Liste', 'name': 'Export Livraison Cmd Online Pdf', 'url': 'export_livraison_cmd_online_pdf'},
 {'categorie': 'Liste', 'name': 'Export Livraisons Excel', 'url': 'export_livraisons_excel'},
 {'categorie': 'Liste', 'name': 'Export Livraisons Pdf', 'url': 'export_livraisons_pdf'},
 {'categorie': 'Liste', 'name': 'Export Pieces Categorie Excel', 'url': 'export_pieces_categorie_excel'},
 {'categorie': 'Liste', 'name': 'Export Pieces Categorie Pdf', 'url': 'export_pieces_categorie_pdf'},
 {'categorie': 'Liste', 'name': 'Export Stock Excel', 'url': 'export_stock_excel'},
 {'categorie': 'Liste', 'name': 'Export Stock Pdf', 'url': 'export_stock_pdf'},
 {'categorie': 'Liste', 'name': 'Export Ventes Excel', 'url': 'export_ventes_excel'},
 {'categorie': 'Liste', 'name': 'Historique Commande', 'url': 'Historique_commande'},
 {'categorie': 'Liste', 'name': 'Historique Global', 'url': 'global_history'},
 {'categorie': 'Liste', 'name': 'Historique Panier', 'url': 'Historique_panier'},
 {'categorie': 'Liste', 'name': 'Historique Pieces', 'url': 'Historique_pieces'},
 {'categorie': 'Liste', 'name': 'liste commande', 'url': 'liste_commandes'},
 {'categorie': 'Liste', 'name': 'Liste des ventes', 'url': 'liste_ventes'},
 {'categorie': 'Liste', 'name': 'Mes ventes', 'url': 'mesventes'},
 {'categorie': 'Liste', 'name': 'Pièce en rupture', 'url': 'piece_rupture'},
 {'categorie': 'Liste', 'name': 'Proforma Attente', 'url': 'proforma_attente'},
 {'categorie': 'Liste', 'name': 'Top ventes', 'url': 'topventes'},
 {'categorie': 'Stocks', 'name': 'Activate Sortie', 'url': 'activate_sortie'},
 {'categorie': 'Stocks', 'name': 'Activate Sortie Local', 'url': 'activate_sortie_local'},
 {'categorie': 'Stocks', 'name': 'Add Categorie', 'url': 'add_categorie'},
 {'categorie': 'Stocks', 'name': 'Add Panier', 'url': 'add_panier'},
 {'categorie': 'Stocks', 'name': 'Add Panier Proforma', 'url': 'add_panier_proforma'},
 {'categorie': 'Stocks', 'name': 'Add Piece', 'url': 'add_piece'},
 {'categorie': 'Stocks', 'name': 'Ajax Add Panier', 'url': 'ajax_add_panier'},
 {'categorie': 'Stocks', 'name': 'Annuler Demande Demandeur', 'url': 'annuler_demande_demandeur'},
 {'categorie': 'Stocks', 'name': 'Annuler Demande Donneur', 'url': 'annuler_demande_donneur'},
 {'categorie': 'Stocks', 'name': 'Bons Commande Paiement', 'url': 'bons_commande_paiement'},
 {'categorie': 'Stocks', 'name': 'Cmd Line Detail', 'url': 'cmd_line_detail'},
 {'categorie': 'Stocks', 'name': 'Deactivate Sortie', 'url': 'deactivate_sortie'},
 {'categorie': 'Stocks', 'name': 'Deactivate Sortie Local', 'url': 'deactivate_sortie_local'},
 {'categorie': 'Stocks', 'name': 'Delet Categorie', 'url': 'delet_categorie'},
 {'categorie': 'Stocks', 'name': 'Delete Perm Category', 'url': 'delete_perm_category'},
 {'categorie': 'Stocks', 'name': 'Details Commande', 'url': 'details_commande'},
 {'categorie': 'Stocks', 'name': 'Download Modele Pieces Excel','url': 'download_modele_pieces_excel'},
 {'categorie': 'Stocks', 'name': 'Entrée stock', 'url': 'entrestock'},
 {'categorie': 'Stocks', 'name': 'Fourniss', 'url': 'fourniss'},
 {'categorie': 'Stocks', 'name': 'Imprimer Bon Commande Vente', 'url': 'imprimer_bon_commande_vente'},
 {'categorie': 'Stocks', 'name': 'Imprimer Bon Livraison', 'url': 'imprimer_bon_livraison'},
 {'categorie': 'Stocks', 'name': 'Imprimer Demande Transfert', 'url': 'imprimer_demande_transfert'},
 {'categorie': 'Stocks', 'name': 'Imprimer Proforma Pdf', 'url': 'imprimer_proforma_pdf'},
 {'categorie': 'Stocks', 'name': 'Imprimer Recu Commande', 'url': 'imprimer_recu_commande'},
 {'categorie': 'Stocks', 'name': 'Info Piece', 'url': 'info_piece'},
 {'categorie': 'Stocks', 'name': 'Livraison Cmd Detail', 'url': 'livraison_cmd_detail'},
 {'categorie': 'Stocks', 'name': 'Modifier sous-catégorie', 'url': 'update_sous_categorie'},
 {'categorie': 'Stocks', 'name': 'Mon stock', 'url': 'stock'},
 {'categorie': 'Stocks', 'name': 'Panier Action', 'url': 'panier_action'},
 {'categorie': 'Stocks', 'name': 'Panier Proforma Action', 'url': 'panier_proforma_action'},
 {'categorie': 'Stocks', 'name': 'Piece Delete', 'url': 'piece_delete'},
 {'categorie': 'Stocks', 'name': 'Piece Detail', 'url': 'piece_detail'},
 {'categorie': 'Stocks', 'name': 'Pieces Archivees', 'url': 'pieces_archivees'},
 {'categorie': 'Stocks', 'name': 'Recevoir Demande Transfert', 'url': 'recevoir_demande_transfert'},
 {'categorie': 'Stocks', 'name': 'Reimprimer Recu Paiement', 'url': 'reimprimer_recu_paiement'},
 {'categorie': 'Stocks', 'name': 'Sous-catégories', 'url': 'add_sous_categorie'},
 {'categorie': 'Stocks', 'name': 'Supprimer sous-catégorie', 'url': 'delete_sous_categorie'},
 {'categorie': 'Stocks', 'name': 'Update Categorie', 'url': 'update_categorie'},
 {'categorie': 'Stocks', 'name': 'Update Fournisseur', 'url': 'update_fournisseur'},
 {'categorie': 'Stocks', 'name': 'Update Perm Category', 'url': 'update_perm_category'},
 {'categorie': 'Stocks', 'name': 'Update Piece', 'url': 'update_piece'},
 {'categorie': 'Stocks', 'name': 'Valid Pay Article', 'url': 'valid_pay_article'},
 {'categorie': 'Stocks', 'name': 'Valider Demande Donneur', 'url': 'valider_demande_donneur'},
 {'categorie': 'Stocks', 'name': 'Valider Livraison', 'url': 'valider_livraison'},
 {'categorie': 'Stocks', 'name': 'Valider Panier Proforma', 'url': 'valider_panier_proforma'},
 {'categorie': 'Stocks', 'name': 'Supprimer Proforma', 'url': 'supprimer_proforma'}]


LOCAL_ENTREPOTS = [{'latitude': '5.4283391', 'longitude': '-4.0188503', 'nom': 'Abobo', 'statut': True},
 {'latitude': '5.3652248', 'longitude': '-3.9170846', 'nom': 'Bingerville', 'statut': True},
 {'latitude': '5.2922260', 'longitude': '-3.9554081', 'nom': 'Koumassi', 'statut': True}]

CRENEAUX_DISPONIBILITE = [{'fermeture': '18:00:00', 'jour': 0, 'local': 'Abobo', 'ouverture': '08:00:00'},
 {'fermeture': '18:00:00', 'jour': 1, 'local': 'Abobo', 'ouverture': '08:00:00'},
 {'fermeture': '18:00:00', 'jour': 2, 'local': 'Abobo', 'ouverture': '08:00:00'},
 {'fermeture': '18:00:00', 'jour': 3, 'local': 'Abobo', 'ouverture': '08:00:00'},
 {'fermeture': '18:00:00', 'jour': 4, 'local': 'Abobo', 'ouverture': '08:00:00'},
 {'fermeture': '12:00:00', 'jour': 5, 'local': 'Abobo', 'ouverture': '08:00:00'},
 {'fermeture': '12:00:00', 'jour': 6, 'local': 'Abobo', 'ouverture': '08:00:00'},
 {'fermeture': '18:00:00', 'jour': 0, 'local': 'Bingerville', 'ouverture': '08:00:00'},
 {'fermeture': '18:00:00', 'jour': 1, 'local': 'Bingerville', 'ouverture': '08:00:00'},
 {'fermeture': '18:00:00', 'jour': 2, 'local': 'Bingerville', 'ouverture': '08:00:00'},
 {'fermeture': '18:00:00', 'jour': 3, 'local': 'Bingerville', 'ouverture': '08:00:00'},
 {'fermeture': '18:00:00', 'jour': 0, 'local': 'Koumassi', 'ouverture': '08:00:00'},
 {'fermeture': '18:00:00', 'jour': 1, 'local': 'Koumassi', 'ouverture': '08:00:00'},
 {'fermeture': '18:00:00', 'jour': 2, 'local': 'Koumassi', 'ouverture': '08:00:00'},
 {'fermeture': '18:00:00', 'jour': 3, 'local': 'Koumassi', 'ouverture': '08:00:00'},
 {'fermeture': '18:00:00', 'jour': 4, 'local': 'Koumassi', 'ouverture': '08:00:00'},
 {'fermeture': '12:00:00', 'jour': 5, 'local': 'Koumassi', 'ouverture': '08:00:00'}]

CATEGORIES = [{'categorie': 'Dzire',
  'description': 'La Suzuki Dzire est une berline compacte offrant un bon compromis entre espace, '
                 'confort et économies, souvent appréciée pour sa praticité et son coffre généreux '
                 'dans sa catégorie.'},
 {'categorie': 'Expresso',
  'description': 'La Suzuki S-Presso est un SUV urbain compact et astucieux, idéal pour la ville '
                 'grâce à sa maniabilité et son style original.'},
 {'categorie': 'Swift',
  'description': 'Grâce à son empattement allongé de 20 mm et son agencement intérieur revu, la '
                 'Suzuki SWIFT offre un espace et un confort incomparables.'}]

SOUS_CATEGORIES = [{'actif': True, 'categorie': 'Dzire', 'nom': 'Moteur', 'ordre': 1, 'slug': 'moteur'},
 {'actif': True, 'categorie': 'Dzire', 'nom': 'Freinage', 'ordre': 2, 'slug': 'freinage'},
 {'actif': True,
  'categorie': 'Dzire',
  'nom': 'Suspension & Direction',
  'ordre': 3,
  'slug': 'suspension-direction'},
 {'actif': True, 'categorie': 'Dzire', 'nom': 'Transmission', 'ordre': 4, 'slug': 'transmission'},
 {'actif': True,
  'categorie': 'Dzire',
  'nom': 'Électricité & Électronique',
  'ordre': 5,
  'slug': 'electricite-electronique'},
 {'actif': True, 'categorie': 'Dzire', 'nom': 'Éclairage', 'ordre': 6, 'slug': 'eclairage'},
 {'actif': True, 'categorie': 'Dzire', 'nom': 'Carrosserie', 'ordre': 7, 'slug': 'carrosserie'},
 {'actif': True,
  'categorie': 'Dzire',
  'nom': 'Climatisation & Chauffage',
  'ordre': 8,
  'slug': 'climatisation-chauffage'},
 {'actif': True, 'categorie': 'Dzire', 'nom': 'Échappement', 'ordre': 9, 'slug': 'echappement'},
 {'actif': True,
  'categorie': 'Dzire',
  'nom': 'Pneus & Jantes',
  'ordre': 10,
  'slug': 'pneus-jantes'},
 {'actif': True,
  'categorie': 'Dzire',
  'nom': 'Refroidissement',
  'ordre': 11,
  'slug': 'refroidissement'},
 {'actif': True,
  'categorie': 'Dzire',
  'nom': 'Accessoires & Intérieur',
  'ordre': 12,
  'slug': 'accessoires-interieur'},
 {'actif': True, 'categorie': 'Swift', 'nom': 'Moteur', 'ordre': 1, 'slug': 'moteur'},
 {'actif': True, 'categorie': 'Swift', 'nom': 'Freinage', 'ordre': 2, 'slug': 'freinage'},
 {'actif': True,
  'categorie': 'Swift',
  'nom': 'Électricité & Électronique',
  'ordre': 5,
  'slug': 'electricite-electronique'},
 {'actif': True, 'categorie': 'Swift', 'nom': 'Éclairage', 'ordre': 6, 'slug': 'eclairage'},
 {'actif': True,
  'categorie': 'Swift',
  'nom': 'Climatisation & Chauffage',
  'ordre': 8,
  'slug': 'climatisation-chauffage'},
 {'actif': True, 'categorie': 'Swift', 'nom': 'Échappement', 'ordre': 9, 'slug': 'echappement'},
 {'actif': True,
  'categorie': 'Swift',
  'nom': 'Pneus & Jantes',
  'ordre': 10,
  'slug': 'pneus-jantes'},
 {'actif': True,
  'categorie': 'Swift',
  'nom': 'Refroidissement',
  'ordre': 11,
  'slug': 'refroidissement'}]

# Snapshot base — zones de livraison e-commerce (pays → villes → communes)
PAYS_LIVRAISON = [
    {'nom': "Côte d'Ivoire", 'code': 'CI', 'actif': True},
]

VILLES_LIVRAISON = [
    {'pays': 'CI', 'nom': 'Abidjan', 'frais_livraison': '2000.00', 'actif': True},
    {'pays': 'CI', 'nom': 'Bingerville', 'frais_livraison': '2500.00', 'actif': True},
]

COMMUNES_LIVRAISON = [
    {'ville': 'Abidjan', 'nom': 'Abobo', 'actif': True},
    {'ville': 'Abidjan', 'nom': 'Adjamé', 'actif': True},
    {'ville': 'Abidjan', 'nom': 'Attécoubé', 'actif': True},
    {'ville': 'Abidjan', 'nom': 'Cocody', 'actif': True},
    {'ville': 'Abidjan', 'nom': 'Koumassi', 'actif': True},
    {'ville': 'Abidjan', 'nom': 'Marcory', 'actif': True},
    {'ville': 'Abidjan', 'nom': 'Plateau', 'actif': True},
    {'ville': 'Abidjan', 'nom': 'Port-Bouët', 'actif': True},
    {'ville': 'Abidjan', 'nom': 'Treichville', 'actif': True},
    {'ville': 'Abidjan', 'nom': 'Yopougon', 'actif': True},
]

# Snapshot base — moyens de paiement (code unique)
MOYENS_PAIEMENT = [
    {'code': 'espece', 'nom': 'Espèce', 'actif': True},
    {'code': 'geniuspay', 'nom': 'Paiement numérique', 'actif': True},
]

# Snapshot base — barèmes de timbre fiscal (tranche par montant_min)
BAREMES_TIMBRE = [
    {'montant_min': '5001.00', 'montant_max': '100000.00', 'montant_timbre': '100.00', 'actif': True},
    {'montant_min': '100001.00', 'montant_max': '500000.00', 'montant_timbre': '500.00', 'actif': True},
    {'montant_min': '500001.00', 'montant_max': '1000000.00', 'montant_timbre': '1000.00', 'actif': True},
    {'montant_min': '1000001.00', 'montant_max': '5000000.00', 'montant_timbre': '2000.00', 'actif': True},
    {'montant_min': '5000001.00', 'montant_max': None, 'montant_timbre': '5000.00', 'actif': True},
]


def _parse_time(value):
    h, m, s = (int(p) for p in value.split(':'))
    return time(h, m, s)


@transaction.atomic
def seed_defaults(*, using='default'):
    from Userauths.models import (
        CreneauDisponibilite,
        CustomPermission,
        LocalEntrepot,
        TypeCustomPermission,
    )
    from ecom.models import CommuneLivraison, PaysLivraison, VilleLivraison
    from stock.models import BaremeTimbre, Categorie, MoyenPaiement, SousCategorie

    types = {}
    for name in TYPE_PERMISSIONS:
        obj, _ = TypeCustomPermission.objects.using(using).get_or_create(
            categorie=name,
            defaults={'categorie': name},
        )
        types[name] = obj

    for item in CUSTOM_PERMISSIONS:
        cat = types.get(item['categorie'])
        if cat is None:
            cat, _ = TypeCustomPermission.objects.using(using).get_or_create(
                categorie=item['categorie'],
                defaults={'categorie': item['categorie']},
            )
            types[item['categorie']] = cat
        perm, created = CustomPermission.objects.using(using).get_or_create(
            url=item['url'],
            defaults={'name': item['name'], 'categorie': cat},
        )
        if not created:
            update = []
            if perm.name != item['name']:
                perm.name = item['name']
                update.append('name')
            if perm.categorie_id != cat.pk:
                perm.categorie = cat
                update.append('categorie')
            if update:
                perm.save(update_fields=update)

    locaux = {}
    for item in LOCAL_ENTREPOTS:
        defaults = {
            'latitude': Decimal(item['latitude']) if item.get('latitude') else None,
            'longitude': Decimal(item['longitude']) if item.get('longitude') else None,
            'statut': item.get('statut', True),
        }
        obj, created = LocalEntrepot.objects.using(using).get_or_create(
            nom=item['nom'],
            defaults=defaults,
        )
        if not created:
            update = []
            if obj.latitude is None and defaults['latitude'] is not None:
                obj.latitude = defaults['latitude']
                update.append('latitude')
            if obj.longitude is None and defaults['longitude'] is not None:
                obj.longitude = defaults['longitude']
                update.append('longitude')
            if update:
                obj.save(update_fields=update)
        locaux[item['nom']] = obj

    for item in CRENEAUX_DISPONIBILITE:
        local = locaux.get(item['local'])
        if local is None:
            continue
        CreneauDisponibilite.objects.using(using).get_or_create(
            local_entrepot=local,
            jour=item['jour'],
            defaults={
                'heure_ouverture': _parse_time(item['ouverture']),
                'heure_fermeture': _parse_time(item['fermeture']),
            },
        )

    categories = {}
    for item in CATEGORIES:
        obj, _ = Categorie.objects.using(using).get_or_create(
            categorie=item['categorie'],
            defaults={'description': item.get('description') or ''},
        )
        categories[item['categorie']] = obj

    for item in SOUS_CATEGORIES:
        parent = categories.get(item['categorie'])
        if parent is None:
            continue
        SousCategorie.objects.using(using).get_or_create(
            categorie=parent,
            nom=item['nom'],
            defaults={
                'slug': item.get('slug') or slugify(item['nom']),
                'ordre': item.get('ordre') or 0,
                'actif': item.get('actif', True),
            },
        )

    pays_livraison = {}
    for item in PAYS_LIVRAISON:
        obj, _ = PaysLivraison.objects.using(using).get_or_create(
            code=item['code'],
            defaults={
                'nom': item['nom'],
                'actif': item.get('actif', True),
            },
        )
        pays_livraison[item['code']] = obj

    villes_livraison = {}
    for item in VILLES_LIVRAISON:
        pays = pays_livraison.get(item['pays'])
        if pays is None:
            continue
        obj, _ = VilleLivraison.objects.using(using).get_or_create(
            pays=pays,
            nom=item['nom'],
            defaults={
                'frais_livraison': Decimal(item['frais_livraison']),
                'actif': item.get('actif', True),
            },
        )
        villes_livraison[item['nom']] = obj

    for item in COMMUNES_LIVRAISON:
        ville = villes_livraison.get(item['ville'])
        if ville is None:
            continue
        CommuneLivraison.objects.using(using).get_or_create(
            ville=ville,
            nom=item['nom'],
            defaults={'actif': item.get('actif', True)},
        )

    for item in MOYENS_PAIEMENT:
        MoyenPaiement.objects.using(using).get_or_create(
            code=item['code'],
            defaults={
                'nom': item['nom'],
                'actif': item.get('actif', True),
            },
        )

    for item in BAREMES_TIMBRE:
        montant_min = Decimal(item['montant_min'])
        montant_max = (
            Decimal(item['montant_max']) if item.get('montant_max') is not None else None
        )
        BaremeTimbre.objects.using(using).get_or_create(
            montant_min=montant_min,
            defaults={
                'montant_max': montant_max,
                'montant_timbre': Decimal(item['montant_timbre']),
                'actif': item.get('actif', True),
            },
        )


def seed_on_migrate(sender, **kwargs):
    if getattr(sender, 'name', None) != 'stock':
        return
    try:
        seed_defaults(using=kwargs.get('using') or 'default')
    except (OperationalError, ProgrammingError):
        return
