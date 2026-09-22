"""Contexte global pour les templates e-commerce."""
from decimal import Decimal
from urllib.parse import urlparse

from django.db.models import Sum
from django.urls import reverse

from stock.models import PanierItem
from stock.stock_local_service import get_prix_unitaire, total_panier_items

from .models import FavoriPiece
from .services import (
    FREE_SHIPPING_THRESHOLD,
    get_local_from_session,
    liste_recherches_populaires,
    liste_recherches_recentes,
    queryset_categories_catalogue,
    queryset_panier_online_actif,
)

MAX_MINI_CART_ITEMS = 8

ECOM_API_PATH_PREFIXES = (
    '/panier/quantite/',
    '/panier/retirer/',
)


def safe_ecom_next_url(request):
    """URL de retour pour les actions panier (évite les endpoints AJAX POST-only)."""
    candidates = []
    referer = request.META.get('HTTP_REFERER', '')
    if referer:
        candidates.append(urlparse(referer).path)
    if request.path:
        candidates.append(request.path)
    for path in candidates:
        if path and path != '/' and not any(
            path.startswith(prefix) for prefix in ECOM_API_PATH_PREFIXES
        ):
            return path
    return reverse('ecom_cart')


def get_ecom_cart_context(request, local=None):
    """Contexte panier (réutilisable pour AJAX mini-panier)."""
    local = local or get_local_from_session(request)
    cart_count = 0
    cart_items = []
    cart_subtotal = Decimal('0')
    shipping_remaining = FREE_SHIPPING_THRESHOLD
    shipping_progress = 0

    if request.user.is_authenticated and local:
        panier = queryset_panier_online_actif(request.user, local).first()
        if panier:
            all_items = list(
                PanierItem.objects.filter(panier=panier)
                .select_related('piece')
                .order_by('-id')
            )
            cart_count = PanierItem.objects.filter(panier=panier).aggregate(
                total=Sum('quantite')
            )['total'] or 0
            for item in all_items[:MAX_MINI_CART_ITEMS]:
                prix = get_prix_unitaire(item.piece, local, item.new_price)
                cart_items.append({
                    'id': item.pk,
                    'piece': item.piece,
                    'quantite': item.quantite,
                    'prix_unitaire': prix,
                    'ligne_total': prix * item.quantite,
                })
            cart_subtotal = total_panier_items(all_items, local, panier=panier)
            if FREE_SHIPPING_THRESHOLD > 0:
                shipping_remaining = max(FREE_SHIPPING_THRESHOLD - cart_subtotal, Decimal('0'))
                shipping_progress = min(
                    int(cart_subtotal / FREE_SHIPPING_THRESHOLD * 100),
                    100,
                )

    return {
        'ecom_cart_count': cart_count,
        'ecom_cart_items': cart_items,
        'ecom_cart_subtotal': cart_subtotal,
        'ecom_shipping_remaining': shipping_remaining,
        'ecom_shipping_progress': shipping_progress,
        'ecom_local': local,
        'ecom_cart_next_url': safe_ecom_next_url(request),
    }


def _is_ecom_request(request):
    match = getattr(request, 'resolver_match', None)
    if match:
        if match.app_name == 'ecom':
            return True
        view_module = getattr(getattr(match, 'func', None), '__module__', '') or ''
        if view_module.startswith('ecom.'):
            return True
    return request.path.startswith('/ecom/')


def ecom_cart(request):
    if not _is_ecom_request(request):
        return {}

    local = get_local_from_session(request)
    cart_ctx = get_ecom_cart_context(request, local)

    favoris_count = 0
    favoris_ids = []
    profile_ctx = {
        'ecom_user_display_name': '',
        'ecom_user_email': '',
        'ecom_user_initials': 'AP',
        'ecom_user_photo_url': '',
        'ecom_user_has_photo': False,
        'ecom_user_verified': False,
    }
    if request.user.is_authenticated:
        favoris_ids = list(
            FavoriPiece.objects.filter(utilisateur=request.user).values_list('piece_id', flat=True)
        )
        favoris_count = len(favoris_ids)
        profile_ctx = _ecom_user_profile_context(request.user)

    return {
        **cart_ctx,
        **profile_ctx,
        'ecom_favoris_count': favoris_count,
        'ecom_favoris_ids': favoris_ids,
        'ecom_categories': queryset_categories_catalogue(),
        'ecom_recherches_recentes': liste_recherches_recentes(request),
        'ecom_recherches_populaires': liste_recherches_populaires(),
    }


def _ecom_user_initials(user):
    first = (user.first_name or '').strip()
    last = (user.last_name or '').strip()
    if first and last:
        return f'{first[0]}{last[0]}'.upper()
    if first:
        return first[:2].upper()
    username = (user.username or '').strip()
    return username[:2].upper() if username else 'AP'


def _ecom_has_custom_photo(profil):
    if not profil or not profil.photo:
        return False
    name = (profil.photo.name or '').replace('\\', '/')
    return bool(name) and 'default' not in name


def _ecom_user_profile_context(user):
    from Userauths.models import ProfilUser

    display = f'{(user.first_name or "").strip()} {(user.last_name or "").strip()}'.strip()
    if not display:
        display = user.username or user.email or 'Client'

    try:
        profil = user.profil
    except Exception:
        profil = ProfilUser.objects.filter(user=user).first()

    has_photo = _ecom_has_custom_photo(profil)
    photo_url = ''
    if has_photo:
        try:
            photo_url = profil.photo.url
        except Exception:
            photo_url = ''
            has_photo = False

    return {
        'ecom_user_display_name': display,
        'ecom_user_email': user.email or '',
        'ecom_user_initials': _ecom_user_initials(user),
        'ecom_user_photo_url': photo_url,
        'ecom_user_has_photo': has_photo,
        'ecom_user_verified': bool(getattr(profil, 'est_verifie', False)) if profil else False,
    }
