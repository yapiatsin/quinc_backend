from django.urls import reverse, NoReverseMatch
from userauths.models import ProfilUser, TypeCustomPermission
from userauths.permissions_utils import EXEMPT_URL_NAMES


def _user_nav_initials(user):
    """Initiales basées sur prénom/nom, sinon username (même logique ecom)."""
    first = (user.first_name or '').strip()
    last = (user.last_name or '').strip()
    if first and last:
        return f'{first[0]}{last[0]}'.upper()
    if first:
        return first[:2].upper()
    username = (user.username or '').strip()
    return username[:2].upper() if username else 'AP'


def _user_has_custom_photo(profil):
    if not profil or not profil.photo:
        return False
    name = (profil.photo.name or '').replace('\\', '/')
    return bool(name) and 'default' not in name


def _user_nav_profile_context(user):
    try:
        profil = user.profil
    except Exception:
        profil = ProfilUser.objects.filter(user=user).first()

    has_photo = _user_has_custom_photo(profil)
    photo_url = ''
    if has_photo:
        try:
            photo_url = profil.photo.url
        except Exception:
            photo_url = ''
            has_photo = False

    localite = ''
    local = getattr(user, 'local_entrepot', None)
    if local is not None:
        localite = str(local)

    return {
        'user_nav_has_photo': has_photo,
        'user_nav_photo_url': photo_url,
        'user_nav_initials': _user_nav_initials(user),
        'user_nav_localite': localite,
    }


# Icônes + libellés d'affichage (aliases historiques → nom unique affiché)
CATEGORY_MENU_CONFIG = {
    'Dashboard': {'icon': 'icon-pie-chart', 'label': 'Dashboard'},
    'Stocks': {'icon': 'icon-social-dropbox', 'label': 'Stocks'},
    'Gestion Stock': {'icon': 'icon-social-dropbox', 'label': 'Stocks'},
    'Interfaces': {'icon': 'icon-screen-desktop', 'label': 'Interfaces'},
    'Interface': {'icon': 'icon-screen-desktop', 'label': 'Interfaces'},
    'Listes': {'icon': 'icon-doc', 'label': 'Listes'},
    'Liste': {'icon': 'icon-doc', 'label': 'Listes'},
    'Comptes': {'icon': 'fa fa-user-circle', 'label': 'Comptes'},
    'Autres': {'icon': 'icon-doc', 'label': 'Autres'},
}

# Ordre d'affichage souhaité (libellé normalisé)
CATEGORY_DISPLAY_ORDER = (
    'Dashboard',
    'Stocks',
    'Interfaces',
    'Listes',
    'Comptes',
    'Autres',
)


def _category_display_label(categorie_name):
    config = CATEGORY_MENU_CONFIG.get(categorie_name, {})
    return config.get('label') or categorie_name


def _category_icon(categorie_name):
    config = CATEGORY_MENU_CONFIG.get(categorie_name, {})
    return config.get('icon', 'icon-doc')


# Icônes des entrées de sous-menu (url_name → Font Awesome 4)
PERMISSION_MENU_ICONS = {
    'add_proforma': 'fa fa-file-text-o',
    'proforma_attente': 'fa fa-clock-o',
    'add_localite': 'fa fa-building-o',
    'zones_livraison': 'fa fa-map-marker',
    'cmd_line': 'fa fa-shopping-cart',
    'livraison_cmd_online': 'fa fa-motorcycle',
    'ecom_chat_inbox': 'fa fa-comments',
    'paniers': 'fa fa-handshake-o',
    'caissiere': 'fa fa-money',
    'livraisons': 'fa fa-truck',
    'stock': 'fa fa-cubes',
    'pieces_archivees': 'fa fa-archive',
    'add_categorie': 'fa fa-folder-o',
    'add_sous_categorie': 'fa fa-sitemap',
    'import_sous_categories_excel': 'fa fa-file-excel-o',
    'add_piece': 'fa fa-cog',
    'entrestock': 'fa fa-plus-square-o',
    'liste_ventes': 'fa fa-line-chart',
    'mesventes': 'fa fa-bar-chart',
    'liste_commandes': 'fa fa-list-alt',
    'topventes': 'fa fa-star',
    'Historique_commande': 'fa fa-history',
    'global_history': 'fa fa-clock-o',
    'liste_transferts': 'fa fa-exchange',
    'bons_commande_paiement': 'fa fa-credit-card',
    'add_compte': 'fa fa-user-plus',
    'compte_client': 'fa fa-users',
    'list_permissions': 'fa fa-lock',
    'tbord': 'fa fa-pie-chart',
}


def _permission_menu_icon(url_name):
    return PERMISSION_MENU_ICONS.get(url_name, 'fa fa-angle-right')


def _filter_menu_permissions(perms):
    """Ne garde que les permissions dont l'URL reverse sans arguments (hors auth)."""
    valid = []
    for perm in perms:
        if perm.url in EXEMPT_URL_NAMES:
            continue
        try:
            reverse(perm.url)
            valid.append(perm)
        except (NoReverseMatch, Exception):
            continue
    return valid


def user_permissions_menu(request):
    """
    Context processor : menu navbar = TypeCustomPermission uniques,
    chacun avec ses CustomPermission (filtrées pour l'utilisateur).
    """
    context = {
        'user_menu_categories': [],
        'user_nav_has_photo': False,
        'user_nav_photo_url': '',
        'user_nav_initials': 'AP',
        'user_nav_localite': '',
    }

    if not request.user.is_authenticated:
        return context

    context.update(_user_nav_profile_context(request.user))

    # Une entrée par TypeCustomPermission (pas de doublon Listes/Liste)
    categories = list(TypeCustomPermission.objects.all().order_by('categorie'))

    # Regrouper les alias (Liste/Listes, etc.) sous un même libellé d'affichage
    # pour éviter deux menus « Listes » si les deux existent en BDD.
    grouped = {}  # label -> {category, permissions, icon, sort_key}
    for category in categories:
        raw_name = category.categorie
        label = _category_display_label(raw_name)
        icon = _category_icon(raw_name)

        if request.user.is_superuser:
            perms_qs = category.cat_permis.all().order_by('name')
        else:
            perms_qs = request.user.custom_permissions.filter(
                categorie=category,
            ).order_by('name')

        valid_perms = _filter_menu_permissions(perms_qs)
        if not valid_perms:
            continue

        if label in grouped:
            # Fusionner les permissions des catégories alias (ex. Liste + Listes)
            existing_urls = {p.url for p in grouped[label]['permissions']}
            for perm in valid_perms:
                if perm.url not in existing_urls:
                    perm.menu_icon = _permission_menu_icon(perm.url)
                    grouped[label]['permissions'].append(perm)
                    existing_urls.add(perm.url)
            if any(p.url == 'cmd_line' for p in grouped[label]['permissions']):
                grouped[label]['has_cmd_line'] = True
            if any(p.url == 'livraison_cmd_online' for p in grouped[label]['permissions']):
                grouped[label]['has_livraison_cmd'] = True
            if any(p.url == 'ecom_chat_inbox' for p in grouped[label]['permissions']):
                grouped[label]['has_chat_client'] = True
        else:
            for perm in valid_perms:
                perm.menu_icon = _permission_menu_icon(perm.url)
            grouped[label] = {
                'category': category,
                'display_label': label,
                'permissions': list(valid_perms),
                'icon': icon,
                'has_cmd_line': any(p.url == 'cmd_line' for p in valid_perms),
                'has_livraison_cmd': any(p.url == 'livraison_cmd_online' for p in valid_perms),
                'has_chat_client': any(p.url == 'ecom_chat_inbox' for p in valid_perms),
            }

    order_index = {name: i for i, name in enumerate(CATEGORY_DISPLAY_ORDER)}

    def sort_key(item):
        label = item['display_label']
        return (order_index.get(label, 100), label.lower())

    menu_categories = sorted(grouped.values(), key=sort_key)
    context['user_menu_categories'] = menu_categories

    try:
        from ecom.services import (
            count_commandes_ligne_en_attente,
            count_livraisons_cmd_ligne_a_livrer,
            count_chat_clients_pending,
        )
        nb_attente = count_commandes_ligne_en_attente(request.user)
        nb_a_livrer = count_livraisons_cmd_ligne_a_livrer(request.user)
        nb_chat = count_chat_clients_pending(request.user)
        context['nb_cmd_ligne_attente'] = nb_attente
        context['nb_livraison_cmd_a_livrer'] = nb_a_livrer
        context['nb_chat_clients_pending'] = nb_chat
        context['nb_interfaces_notif'] = nb_attente + nb_a_livrer + nb_chat
    except Exception:
        context['nb_cmd_ligne_attente'] = 0
        context['nb_livraison_cmd_a_livrer'] = 0
        context['nb_chat_clients_pending'] = 0
        context['nb_interfaces_notif'] = 0

    return context
