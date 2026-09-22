"""
Utilitaires pour la gestion des permissions personnalisées.
"""
from django.urls import resolve

# Chemins système jamais soumis à CustomPermission
ALWAYS_EXEMPT_PATH_PREFIXES = (
    '/admin/',
    '/static/',
    '/media/',
    '/__debug__/',
    '/api/',
    '/i18n/',
    '/webhooks/',
)

# Auth publique + compte personnel (userauths urls L5–18) — jamais CustomPermission
EXEMPT_URL_NAMES = frozenset({
    'pbholdingsite',
    'connexion',
    'register',
    'activate-account',
    'resend-activation',
    'deconnexion',
    'forgot',
    'otp',
    'request_email',
    'verify_otp',
    'password_change',
    'password_change_done',
    'change_password',
    'ajax_sous_categories',
    'marquer_notification_lue',
    'marquer_toutes_notifications_lues',
    'get_notifications',
    'liste_notifications',
    'notification_detail',
    'notification_delete',
})

# Comptes staff/clients mag + permissions + localités (userauths urls L19–50)
PROTECTED_userauths_URL_NAMES = frozenset({
    'add_compte',
    'export_comptes_excel',
    'compte_client',
    'export_comptes_clients_excel',
    'detail_compte_client',
    'fiche_client_pdf',
    'update_compte_client',
    'del_compte_client',
    'update_compte',
    'active_compte',
    'deactive_compte',
    'del_compte',
    'list_perm_categories',
    'create_perm_category',
    'update_perm_category',
    'delete_perm_category',
    'add_localite',
    'update_localite',
    'delete_localite',
})

STOCK_PATH_PREFIX = '/stocks/'

# Actions techniques de la caisse couvertes par la permission d'interface « caissiere ».
# Sans cela, un caissier peut ouvrir /caisse/paiement/ mais le POST de validation
# (valid_pay_article) est refusé en 403 → « Erreur lors du paiement ».
CAISSE_IMPLIED_BY_CAISSIERE = frozenset({
    'valid_pay_article',
    'caisse_geniuspay_retour',
    'caisse_geniuspay_statut',
    'ajax_caisse_detail',
    'ajax_reload_paniers',
    'ajax_calcul_timbre',
    'reimprimer_recu_paiement',
    'imprimer_bon_commande_vente',
    'imprimer_recu_commande',
    'export_caisse_ventes_excel',
    'export_caisse_ventes_pdf',
})

# parent_url -> urls enfants autorisées si l'utilisateur a le parent
IMPLIED_PERMISSIONS = {
    'caissiere': CAISSE_IMPLIED_BY_CAISSIERE,
}


def get_permission_name_from_url(request):
    """
    Récupère le nom de permission depuis l'URL de la requête
    (name dans urls.py).
    """
    try:
        resolver_match = resolve(request.path_info)
        return resolver_match.url_name
    except Exception:
        return None


def user_has_permission(user, permission_url):
    """Vérifie si un utilisateur a une permission spécifique (ou une permission parente)."""
    if not user.is_authenticated:
        return False
    if user.is_superuser:
        return True
    if user.custom_permissions.filter(url=permission_url).exists():
        return True
    for parent, children in IMPLIED_PERMISSIONS.items():
        if permission_url in children and user.custom_permissions.filter(url=parent).exists():
            return True
    return False


def _path_has_exempt_prefix(path):
    return any(path.startswith(prefix) for prefix in ALWAYS_EXEMPT_PATH_PREFIXES)


def is_stock_path(request):
    """True si la requête cible le module magasin (/stocks/)."""
    return request.path.startswith(STOCK_PATH_PREFIX)


def is_protected_userauths_url(url_name):
    return bool(url_name) and url_name in PROTECTED_userauths_URL_NAMES


def is_permission_check_exempt(request, url_name=None):
    """
    True si CustomPermission ne doit pas s'appliquer
    (chemins système, URL auth/profil, etc.).
    """
    if _path_has_exempt_prefix(request.path):
        return True
    if url_name is None:
        url_name = get_permission_name_from_url(request)
    if url_name and url_name in EXEMPT_URL_NAMES:
        return True
    return False


def should_enforce_custom_permission(request, url_name=None):
    """
    True uniquement pour le périmètre protégé :
    - toutes les URLs nommées sous /stocks/
    - URLs userauths comptes / permissions / localités
    """
    if url_name is None:
        url_name = get_permission_name_from_url(request)

    if is_permission_check_exempt(request, url_name):
        return False

    if is_stock_path(request):
        return bool(url_name)

    if is_protected_userauths_url(url_name):
        return True

    return False
