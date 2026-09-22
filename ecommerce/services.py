"""Services métier e-commerce (panier en ligne, commande, notifications)."""
from __future__ import annotations

from decimal import Decimal

from django.db import transaction
from django.db.models import Count, Prefetch, Q, Sum, Exists, OuterRef
from django.utils import timezone

from Userauths.models import CustomUser, LocalEntrepot
from stock.models import (
    Categorie,
    SousCategorie,
    Commande,
    MoyenPaiement,
    Notification,
    Panier,
    PanierItem,
    Piece,
    StockLocal,
    Ticket,
)
from stock.stock_local_service import (
    annotate_pieces_for_localite,
    decrementer_stock,
    filter_piece_catalogue_actif,
    filter_pieces_avec_stock,
    filter_pieces_categories_actives,
    get_prix_unitaire,
    get_quantite_vendable,
    piece_visible_en_localite,
    total_panier_items,
)

SESSION_LOCAL_KEY = 'ecom_local_id'
FREE_SHIPPING_THRESHOLD = Decimal('125000')
ROLES_STAFF_CMD = {'accueil', 'gestionnaire', 'chefagence', 'admin'}
ROLES_LIVREUR = {'livreur'}
ROLES_MAGASIN = {'accueil', 'caissier', 'livreur', 'chefagence', 'gestionnaire', 'admin'}
# Rôles qui voient les commandes de leur LocalEntrepot (pas toutes les assignations)
ROLES_VUE_LOCAL_ENTREPOT = {'accueil', 'caissier', 'chefagence', 'gestionnaire'}

def _is_admin_global(user) -> bool:
    return bool(
        getattr(user, 'is_superuser', False)
        or getattr(user, 'role', None) == 'admin'
    )

def filter_commandes_livraison_online_for_user(user, qs, localite=None):
    """
    Périmètre des commandes en ligne (livraison) selon le rôle :
    - livreur : uniquement les commandes qui lui sont assignées
    - accueil / caissier / chefagence (/ gestionnaire) : LocalEntrepot de l'utilisateur
    - admin / superuser : tout (filtre localité optionnel si fourni)
    """
    if not getattr(user, 'is_authenticated', False):
        return qs.none()

    role = getattr(user, 'role', None)

    if _is_admin_global(user):
        if localite is not None:
            return qs.filter(panier__local_entrepot=localite)
        return qs

    if role in ROLES_LIVREUR:
        return qs.filter(livreur=user)

    if role in ROLES_VUE_LOCAL_ENTREPOT:
        local = getattr(user, 'local_entrepot', None)
        if not local:
            return qs.none()
        return qs.filter(panier__local_entrepot=local)

    return qs.none()


def count_commandes_ligne_en_attente(user) -> int:
    """Nombre de commandes en ligne encore en attente (badge navbar)."""
    if not getattr(user, 'is_authenticated', False):
        return 0
    role = getattr(user, 'role', None)
    if not (user.is_superuser or role in ROLES_STAFF_CMD):
        return 0
    qs = Commande.objects.filter(
        commande_en_ligne=True,
        statut_commande='en_attente',
    )
    if not _is_admin_global(user):
        local = getattr(user, 'local_entrepot', None)
        if not local:
            return 0
        qs = qs.filter(panier__local_entrepot=local)
    return qs.count()


def count_livraisons_cmd_ligne_a_livrer(user) -> int:
    """Nombre de commandes en ligne assignées encore à livrer (badge navbar)."""
    if not getattr(user, 'is_authenticated', False):
        return 0
    role = getattr(user, 'role', None)
    if not (user.is_superuser or role in ROLES_MAGASIN):
        return 0
    qs = Commande.objects.filter(
        commande_en_ligne=True,
        livreur__isnull=False,
    ).exclude(statut_commande__in=['livrer', 'annuler'])
    return filter_commandes_livraison_online_for_user(user, qs).count()


def count_chat_clients_pending(user) -> int:
    """Nombre de conversations chat en attente (badge navbar)."""
    if not getattr(user, 'is_authenticated', False):
        return 0
    role = getattr(user, 'role', None)
    if not (user.is_superuser or role in ROLES_STAFF_CMD):
        return 0
    from ecom import chatbots
    from ecom.models import ChatConversation
    return chatbots.staff_list_conversations(
        user, status=ChatConversation.STATUS_PENDING
    ).count()


def liste_pays_actifs():
    from .models import PaysLivraison
    return PaysLivraison.objects.filter(actif=True).order_by('nom')


def liste_villes_actives(pays_id=None):
    from .models import VilleLivraison
    qs = VilleLivraison.objects.filter(actif=True, pays__actif=True).select_related('pays')
    if pays_id:
        qs = qs.filter(pays_id=pays_id)
    return qs.order_by('nom')


def liste_communes_actives(ville_id=None):
    from .models import CommuneLivraison
    qs = CommuneLivraison.objects.filter(actif=True, ville__actif=True, ville__pays__actif=True)
    if ville_id:
        qs = qs.filter(ville_id=ville_id)
    return qs.select_related('ville').order_by('nom')


def get_frais_livraison(ville, sous_total: Decimal, mode_reception: str = 'livraison') -> Decimal:
    """Retourne les frais de livraison (0 si retrait, seuil gratuit, ou ville inactive)."""
    if mode_reception != 'livraison':
        return Decimal('0.00')
    sous_total = Decimal(str(sous_total or 0))
    if sous_total >= FREE_SHIPPING_THRESHOLD:
        return Decimal('0.00')
    if ville is None or not getattr(ville, 'actif', False):
        return Decimal('0.00')
    if not getattr(ville, 'pays', None) or not ville.pays.actif:
        return Decimal('0.00')
    return Decimal(str(ville.frais_livraison or 0))


def frais_livraison_payload(ville, sous_total: Decimal, mode_reception: str = 'livraison') -> dict:
    frais = get_frais_livraison(ville, sous_total, mode_reception)
    gratuit = mode_reception == 'livraison' and Decimal(str(sous_total or 0)) >= FREE_SHIPPING_THRESHOLD
    return {
        'frais': float(frais),
        'frais_affiche': f'{frais:,.0f}'.replace(',', ' '),
        'gratuit': gratuit,
        'seuil_gratuit': float(FREE_SHIPPING_THRESHOLD),
        'ville_nom': ville.nom if ville else '',
    }


def get_local_from_session(request) -> LocalEntrepot | None:
    local_id = request.session.get(SESSION_LOCAL_KEY)
    if not local_id:
        return None
    return LocalEntrepot.objects.filter(code=local_id).first()


def set_local_in_session(request, local: LocalEntrepot) -> None:
    request.session[SESSION_LOCAL_KEY] = str(local.code)


def quantite_disponible_piece(piece: Piece, local: LocalEntrepot | None = None) -> int:
    """
    Source unique du stock affiché / vendable en ecom.

    Ordre de résolution (mêmes règles partout) :
    1. Prefetch ``stocks_disponibles`` (boutique / index)
    2. Annotation ``quantite_disponible`` (+ ``active_sortie_local``) si localité
    3. Lecture StockLocal active_sortie=True (get_quantite_vendable)
    """
    stocks = getattr(piece, 'stocks_disponibles', None)
    if stocks is not None:
        if local is None:
            return sum(int(s.quantite_disponible or 0) for s in stocks)
        for stock in stocks:
            if stock.local_entrepot_id == local.pk:
                return int(stock.quantite_disponible or 0)
        return 0

    if local is not None and hasattr(piece, 'quantite_disponible'):
        if getattr(piece, 'active_sortie_local', True) is False:
            return 0
        return int(piece.quantite_disponible or 0)

    if local is None and getattr(piece, 'stock_total', None) is not None:
        return int(piece.stock_total or 0)

    return int(get_quantite_vendable(piece, local) or 0)


def enrichir_piece_stock(piece: Piece, local: LocalEntrepot | None = None) -> Piece:
    """Attache quantite_disponible cohérente sur l'instance Piece (détail, etc.)."""
    piece.quantite_disponible = quantite_disponible_piece(piece, local)
    return piece


def queryset_pieces_local(local: LocalEntrepot):
    from stock.models import Piece as PieceModel

    stocks_qs = StockLocal.objects.filter(
        active_sortie=True,
        quantite_disponible__gt=0,
    ).select_related('local_entrepot').order_by('local_entrepot__nom')

    qs = PieceModel.objects.select_related('categorie', 'sous_categorie').prefetch_related(
        Prefetch('stocks', queryset=stocks_qs, to_attr='stocks_disponibles'),
    )
    qs = annotate_pieces_for_localite(qs, local)
    return filter_pieces_avec_stock(qs, local).order_by('designation')


def queryset_piece_fiche():
    """
    Pièce catalogue active pour la fiche détail.

    Inclut les pièces en rupture de stock (affichage « Rupture »),
    contrairement à queryset_pieces_toutes_localites (listing boutique).
    """
    stocks_qs = StockLocal.objects.filter(
        active_sortie=True,
        quantite_disponible__gt=0,
    ).select_related('local_entrepot').order_by('local_entrepot__nom')

    qs = Piece.objects.select_related('categorie', 'sous_categorie').prefetch_related(
        Prefetch('stocks', queryset=stocks_qs, to_attr='stocks_disponibles'),
        'images_supplementaires',
    )
    return filter_pieces_categories_actives(filter_piece_catalogue_actif(qs)).annotate(
        stock_total=Sum(
            'stocks__quantite_disponible',
            filter=Q(stocks__active_sortie=True, stocks__quantite_disponible__gt=0),
        ),
    )


def queryset_pieces_toutes_localites():
    """Pièces en stock actif, avec le détail par localité (prefetch)."""
    return (
        queryset_piece_fiche()
        .filter(
            stocks__active_sortie=True,
            stocks__quantite_disponible__gt=0,
        )
        .distinct()
        .order_by('designation')
    )


def pieces_pour_catalogue(local: LocalEntrepot | None = None, *, limit: int | None = None):
    """Liste de pièces avec stocks par localité et prix d'affichage."""
    qs = queryset_pieces_toutes_localites()
    if limit:
        qs = qs[:limit]
    pieces = list(qs)
    for piece in pieces:
        piece.prix_affiche = (
            get_prix_unitaire(piece, local) if local else piece.prix_unitaire
        )
    return pieces


MAX_PIECES_PLUS_COMMANDEES = 12


def _top_piece_ids_par_quantite(panier_items_qs, *, limit: int) -> list[int]:
    """Retourne les IDs de pièces triés par quantité totale décroissante."""
    rows = (
        panier_items_qs.values('piece_id')
        .annotate(total_qte=Sum('quantite'))
        .order_by('-total_qte')[:limit]
    )
    return [row['piece_id'] for row in rows if row.get('piece_id')]


def queryset_top_pieces_vendues(local: LocalEntrepot | None = None):
    """PanierItems des ventes payées (source prioritaire)."""
    qs = PanierItem.objects.filter(
        panier__valide=True,
        panier__panier_paye=True,
    )
    if local is not None:
        qs = qs.filter(panier__local_entrepot=local)
    return qs


def queryset_top_pieces_commandees(local: LocalEntrepot | None = None):
    """PanierItems des commandes non annulées (fallback si pas assez de ventes)."""
    qs = PanierItem.objects.filter(
        Q(panier__valide=True)
        | Q(panier__commands__statut_commande__in=['en_attente', 'valider', 'livrer']),
    )
    if local is not None:
        qs = qs.filter(panier__local_entrepot=local)
    return qs.distinct()


def _collect_top_piece_ids(local: LocalEntrepot | None, *, limit: int) -> list[int]:
    """IDs top ventes puis commandes, pour une localité (ou global)."""
    piece_ids = _top_piece_ids_par_quantite(
        queryset_top_pieces_vendues(local),
        limit=limit,
    )
    if len(piece_ids) < limit:
        for pid in _top_piece_ids_par_quantite(
            queryset_top_pieces_commandees(local),
            limit=limit,
        ):
            if pid not in piece_ids:
                piece_ids.append(pid)
            if len(piece_ids) >= limit:
                break
    return piece_ids


def lignes_pieces_plus_commandees(
    local: LocalEntrepot | None = None,
    *,
    limit: int = MAX_PIECES_PLUS_COMMANDEES,
):
    """
    Pièces les plus vendues, sinon les plus commandées.

    Priorité :
    1. Ventes (panier validé + payé)
    2. Commandes (panier validé ou lié à une Commande non annulée)
    3. Si localité sans historique : classement global

    Retourne des lignes compatibles avec les cartes catalogue
    (stock via quantite_disponible_piece).
    """
    piece_ids = _collect_top_piece_ids(local, limit=limit)
    if local is not None and len(piece_ids) < limit:
        for pid in _collect_top_piece_ids(None, limit=limit):
            if pid not in piece_ids:
                piece_ids.append(pid)
            if len(piece_ids) >= limit:
                break

    if not piece_ids:
        return []

    # Inclure aussi les pièces encore référencées même si stock épuisé
    pieces_qs = (
        Piece.objects.select_related('categorie', 'sous_categorie')
        .prefetch_related(
            Prefetch(
                'stocks',
                queryset=StockLocal.objects.filter(
                    active_sortie=True,
                    quantite_disponible__gt=0,
                ).select_related('local_entrepot').order_by('local_entrepot__nom'),
                to_attr='stocks_disponibles',
            ),
        )
        .filter(pk__in=piece_ids)
        .filter(Q(active_sortie=True) | Q(active_sortie__isnull=True))
    )
    catalogue = {p.pk: p for p in pieces_qs}
    rows = []
    for pid in piece_ids:
        piece = catalogue.get(pid)
        if not piece:
            continue
        quantite = quantite_disponible_piece(piece, local)
        enrichir_piece_stock(piece, local)
        rows.append({
            'piece': piece,
            'prix_affiche': get_prix_unitaire(piece, local) if local else piece.prix_unitaire,
            'show_all_stocks': local is None,
            'quantite': quantite if local is not None else None,
            'localite_code': str(local.code) if local else '',
            'localite_nom': local.nom if local else '',
        })
        if len(rows) >= limit:
            break
    return rows


def _qs_pieces_menu_boutique():
    return (
        filter_pieces_categories_actives(
            Piece.objects.filter(
                Q(active_sortie=True) | Q(active_sortie__isnull=True),
                stocks__active_sortie=True,
                stocks__quantite_disponible__gt=0,
            )
        )
        .distinct()
        .only('id', 'designation', 'numero_piece', 'categorie_id', 'sous_categorie_id')
        .order_by('designation')
    )


def queryset_categories_catalogue():
    """Catégories uniques ayant au moins une pièce en stock (toutes localités)."""
    pieces_en_stock = Piece.objects.filter(
        categorie=OuterRef('pk'),
    ).filter(
        Q(active_sortie=True) | Q(active_sortie__isnull=True),
        stocks__active_sortie=True,
        stocks__quantite_disponible__gt=0,
    )
    sous_qs = (
        SousCategorie.objects.filter(actif=True)
        .order_by('ordre', 'nom')
        .prefetch_related(
            Prefetch(
                'pieces',
                queryset=_qs_pieces_menu_boutique(),
                to_attr='menu_pieces',
            )
        )
    )
    return (
        Categorie.objects.filter(Exists(pieces_en_stock), actif=True)
        .prefetch_related(
            Prefetch('sous_categories', queryset=sous_qs),
            Prefetch(
                'piece_set',
                queryset=_qs_pieces_menu_boutique().filter(sous_categorie__isnull=True),
                to_attr='menu_pieces_sans_sous',
            ),
        )
        .order_by('categorie')
    )


def catalogue_groupe_par_localite(pieces, localites):
    """Regroupe les pièces par localité pour les onglets catalogue."""
    grouped = {'all': list(pieces)}
    for loc in localites:
        code = str(loc.code)
        rows = []
        for piece in pieces:
            quantite = quantite_disponible_piece(piece, loc)
            if quantite > 0:
                rows.append({
                    'piece': piece,
                    'quantite': quantite,
                    'prix': get_prix_unitaire(piece, loc),
                    'prix_affiche': get_prix_unitaire(piece, loc),
                    'localite': loc,
                    'localite_code': code,
                    'localite_nom': loc.nom,
                })
        grouped[code] = rows
    return grouped


def lignes_catalogue_toutes_localites(pieces, localites):
    """Une carte par (pièce, localité) où la pièce est en stock."""
    rows = []
    for piece in pieces:
        for loc in localites:
            quantite = quantite_disponible_piece(piece, loc)
            if quantite > 0:
                rows.append({
                    'piece': piece,
                    'prix_affiche': get_prix_unitaire(piece, loc),
                    'show_all_stocks': False,
                    'quantite': quantite,
                    'localite_code': str(loc.code),
                    'localite_nom': loc.nom,
                })
    return rows


def filtrer_pieces_boutique(request):
    """Filtres GET pour la page boutique."""
    qs = queryset_pieces_toutes_localites()
    q = request.GET.get('q', '').strip()
    if q:
        qs = qs.filter(
            Q(designation__icontains=q)
            | Q(numero_piece__icontains=q)
            | Q(categorie__categorie__icontains=q)
            | Q(sous_categorie__nom__icontains=q)
        )
    # Mobile API envoie `cid` (ex. c214b4) ; le web boutique envoie le pk numérique.
    pk_ids = []
    cids = []
    for raw in request.GET.getlist('categorie'):
        raw = str(raw).strip()
        if not raw:
            continue
        try:
            pk_ids.append(int(raw))
        except (TypeError, ValueError):
            cids.append(raw)
    if pk_ids or cids:
        q_cat = Q()
        if pk_ids:
            q_cat |= Q(categorie_id__in=pk_ids)
        if cids:
            q_cat |= Q(categorie__cid__in=cids)
        qs = qs.filter(q_cat)
    sous_ids = []
    sous_sids = []
    for raw in request.GET.getlist('sous_categorie'):
        raw = str(raw).strip()
        if not raw:
            continue
        try:
            sous_ids.append(int(raw))
        except (TypeError, ValueError):
            sous_sids.append(raw)
    if sous_ids or sous_sids:
        q_sous = Q()
        if sous_ids:
            q_sous |= Q(sous_categorie_id__in=sous_ids)
        if sous_sids:
            q_sous |= Q(sous_categorie__sid__in=sous_sids)
        qs = qs.filter(q_sous)
    prix_min = request.GET.get('prix_min', '').strip()
    prix_max = request.GET.get('prix_max', '').strip()
    try:
        if prix_min:
            qs = qs.filter(prix_unitaire__gte=Decimal(prix_min))
        if prix_max:
            qs = qs.filter(prix_unitaire__lte=Decimal(prix_max))
    except Exception:
        pass
    localite_code = request.GET.get('localite', '').strip()
    if localite_code:
        qs = qs.filter(
            stocks__local_entrepot__code=localite_code,
            stocks__active_sortie=True,
            stocks__quantite_disponible__gt=0,
        ).distinct()
    return qs


def lignes_catalogue_shop(pieces, local=None, *, filter_local=None):
    """Lignes d'affichage pour les cartes boutique / détail localité."""
    display_local = filter_local or local
    rows = []
    for piece in pieces:
        prix = get_prix_unitaire(piece, display_local) if display_local else piece.prix_unitaire
        localite_code = ''
        localite_nom = ''
        if display_local:
            localite_code = str(display_local.code)
            localite_nom = display_local.nom
            quantite = quantite_disponible_piece(piece, display_local)
        else:
            # Sans localité : laisser None pour afficher le détail par localité
            quantite = None
        enrichir_piece_stock(piece, display_local)
        rows.append({
            'piece': piece,
            'prix_affiche': prix,
            'show_all_stocks': not display_local,
            'quantite': quantite,
            'localite_code': localite_code,
            'localite_nom': localite_nom,
        })
    return rows


def get_local_from_code(code: str):
    if not code:
        return None
    return LocalEntrepot.objects.filter(code=code).first()


def filtrer_localites_catalogue(request):
    """Recherche / tri pour la page grille des localités."""
    qs = LocalEntrepot.objects.annotate(
        nb_pieces=Count(
            'stocks',
            filter=Q(
                stocks__active_sortie=True,
                stocks__quantite_disponible__gt=0,
            ),
            distinct=True,
        ),
    )
    q = request.GET.get('q', '').strip()
    if q:
        qs = qs.filter(Q(nom__icontains=q))
    sort = request.GET.get('sort', 'nom')
    if sort == 'pieces':
        qs = qs.order_by('-nb_pieces', 'nom')
    else:
        qs = qs.order_by('nom')
    return qs


def categories_pour_localite(local: LocalEntrepot):
    """Catégories ayant du stock dans une localité donnée."""
    sous_qs = SousCategorie.objects.filter(actif=True).order_by('ordre', 'nom')
    return (
        Categorie.objects.filter(
            actif=True,
            piece__stocks__local_entrepot=local,
            piece__stocks__active_sortie=True,
            piece__stocks__quantite_disponible__gt=0,
        )
        .distinct()
        .prefetch_related(Prefetch('sous_categories', queryset=sous_qs))
        .order_by('categorie')
    )


def queryset_panier_online_actif(user, local: LocalEntrepot, *, for_update=False):
    qs = Panier.objects.filter(
        utilisateur=user,
        local_entrepot=local,
        commande_en_ligne=True,
        valide=False,
        proforma=False,
        panier_paye=False,
    )
    if for_update:
        qs = qs.select_for_update()
    return qs.order_by('-date_save')


@transaction.atomic
def get_or_create_panier_online(user, local: LocalEntrepot):
    panier = queryset_panier_online_actif(user, local, for_update=True).first()
    if panier:
        return panier, False
    panier = Panier.objects.create(
        utilisateur=user,
        local_entrepot=local,
        commande_en_ligne=True,
        valide=False,
        proforma=False,
    )
    return panier, True


def get_moyen_espece() -> MoyenPaiement:
    moyen, _ = MoyenPaiement.objects.get_or_create(
        code='espece',
        defaults={'nom': 'Espèces', 'actif': True},
    )
    return moyen


def get_moyen_geniuspay() -> MoyenPaiement:
    moyen, _ = MoyenPaiement.objects.get_or_create(
        code='geniuspay',
        defaults={'nom': 'Paiement numérique', 'actif': True},
    )
    return moyen


def moyen_depuis_code(code: str | None) -> MoyenPaiement:
    code = (code or 'espece').strip().lower()
    if code == 'geniuspay':
        return get_moyen_geniuspay()
    return get_moyen_espece()


@transaction.atomic
def ajouter_au_panier_online(user, local: LocalEntrepot, piece: Piece, quantite: int = 1):
    if not piece_visible_en_localite(piece, local):
        raise ValueError('Cette pièce n\'est pas disponible dans cette localité.')
    stock_dispo = quantite_disponible_piece(piece, local)
    if stock_dispo <= 0:
        raise ValueError('Cette pièce n\'est pas disponible dans cette localité.')
    panier, _ = get_or_create_panier_online(user, local)
    item = PanierItem.objects.filter(panier=panier, piece=piece).first()
    qte_finale = quantite if item is None else item.quantite + quantite
    if qte_finale > stock_dispo:
        raise ValueError(f'Stock insuffisant ({stock_dispo} disponible).')
    prix = get_prix_unitaire(piece, local)
    if item:
        item.quantite = qte_finale
        item.new_price = prix
        item.save()
    else:
        PanierItem.objects.create(
            panier=panier,
            piece=piece,
            quantite=quantite,
            new_price=prix,
        )
    return panier


@transaction.atomic
def mettre_a_jour_quantite_panier(user, local: LocalEntrepot, item_id: int, quantite: int):
    panier = queryset_panier_online_actif(user, local, for_update=True).first()
    if not panier:
        raise ValueError('Panier introuvable.')
    item = PanierItem.objects.select_related('piece').get(pk=item_id, panier=panier)
    if quantite <= 0:
        item.delete()
        return panier
    stock_dispo = quantite_disponible_piece(item.piece, local)
    if quantite > stock_dispo:
        raise ValueError(f'Stock insuffisant ({stock_dispo} disponible).')
    item.quantite = quantite
    item.save()
    return panier


@transaction.atomic
def definir_quantite_panier_piece(user, local: LocalEntrepot, piece: Piece, quantite: int):
    """Fixe la quantité d'une pièce dans le panier local (0 = retirer la ligne)."""
    panier = queryset_panier_online_actif(user, local, for_update=True).first()
    item = None
    if panier:
        item = PanierItem.objects.filter(panier=panier, piece=piece).first()

    if quantite <= 0:
        if item:
            item.delete()
        return panier, 0

    if not piece_visible_en_localite(piece, local):
        raise ValueError('Cette pièce n\'est pas disponible dans cette localité.')
    stock_dispo = quantite_disponible_piece(piece, local)
    if stock_dispo <= 0:
        raise ValueError('Cette pièce n\'est pas disponible dans cette localité.')
    if quantite > stock_dispo:
        raise ValueError(
            f'Stock insuffisant à {local.nom} : {stock_dispo} disponible(s).'
        )

    prix = get_prix_unitaire(piece, local)
    if item:
        item.quantite = quantite
        item.new_price = prix
        item.save()
    else:
        panier, _ = get_or_create_panier_online(user, local)
        PanierItem.objects.create(
            panier=panier,
            piece=piece,
            quantite=quantite,
            new_price=prix,
        )
    return panier, quantite


@transaction.atomic
def retirer_du_panier(user, local: LocalEntrepot, item_id: int):
    panier = queryset_panier_online_actif(user, local, for_update=True).first()
    if not panier:
        return None
    PanierItem.objects.filter(pk=item_id, panier=panier).delete()
    return panier


def notifier_commande_en_ligne(commande: Commande) -> None:
    local = commande.panier.local_entrepot
    if not local:
        return
    staff = CustomUser.objects.filter(
        local_entrepot=local,
        role__in=ROLES_STAFF_CMD,
        is_active=True,
    )
    for user in staff:
        Notification.objects.create(
            type_notification='commande_en_ligne',
            titre='Nouvelle commande en ligne',
            message=(
                f'Commande {commande.numero_commande} — '
                f'{commande.total:,.0f} FCFA — client '
                f'{commande.panier.utilisateur.username if commande.panier.utilisateur else "—"}'
            ),
            utilisateur=user,
        )


def _publish_cmd_ligne_mqtt(commande: Commande, event: str) -> None:
    """Publie un événement MQTT après commit (pages cmd_line / livraison_cmd_online)."""
    commande_id = commande.pk
    numero = commande.numero_commande
    total = commande.total
    statut = commande.statut_commande
    livreur_id = commande.livreur_id
    paye = commande.paye
    panier = getattr(commande, 'panier', None)
    local = getattr(panier, 'local_entrepot', None) if panier else None
    local_id = getattr(local, 'pk', None) if local else None
    local_nom = getattr(local, 'nom', None) if local else None

    def _do():
        try:
            from stock.mqtt_service import publish_commande_ligne
            publish_commande_ligne(
                event,
                commande_id,
                numero,
                total=total,
                statut=statut,
                local_entrepot_id=local_id,
                local_entrepot_nom=local_nom,
                livreur_id=livreur_id,
                paye=paye,
            )
        except Exception:
            import logging
            logging.getLogger(__name__).exception(
                "Échec publication Pusher commande_ligne event=%s commande=%s",
                event, numero,
            )

    transaction.on_commit(_do)


@transaction.atomic
def valider_commande_online(
    user,
    local: LocalEntrepot,
    mode_reception: str = 'livraison',
    *,
    pays=None,
    ville=None,
    commune=None,
    adresse_domicile: str = '',
    telephone_livraison: str = '',
    instruction_livraison: str = '',
    moyen_paiement: str | MoyenPaiement | None = 'espece',
):
    panier = queryset_panier_online_actif(user, local, for_update=True).first()
    if not panier:
        raise ValueError('Votre panier est vide.')
    items = list(PanierItem.objects.filter(panier=panier).select_related('piece'))
    if not items:
        raise ValueError('Votre panier est vide.')
    for item in items:
        if quantite_disponible_piece(item.piece, local) < item.quantite:
            raise ValueError(f'Stock insuffisant pour {item.piece.designation}.')

    mode_reception = mode_reception or 'livraison'
    if mode_reception not in ('livraison', 'retrait'):
        mode_reception = 'livraison'

    sous_total = total_panier_items(items, local, panier=panier)
    frais = Decimal('0.00')

    if mode_reception == 'livraison':
        if not pays or not getattr(pays, 'actif', False):
            raise ValueError('Veuillez sélectionner un pays de livraison valide.')
        if not ville or not getattr(ville, 'actif', False) or ville.pays_id != pays.pk:
            raise ValueError('Veuillez sélectionner une ville de livraison valide.')
        if not commune or not getattr(commune, 'actif', False) or commune.ville_id != ville.pk:
            raise ValueError('Veuillez sélectionner une commune.')
        adresse_domicile = (adresse_domicile or '').strip()
        if not adresse_domicile:
            raise ValueError('Veuillez préciser votre adresse de domicile.')
        telephone_livraison = (telephone_livraison or '').strip() or (user.contact or '')
        if not telephone_livraison:
            raise ValueError('Veuillez indiquer un téléphone de livraison.')
        frais = get_frais_livraison(ville, sous_total, mode_reception)
        panier.livraison_pays = pays
        panier.livraison_ville = ville
        panier.livraison_commune = commune
        panier.adresse_domicile = adresse_domicile
        panier.telephone_livraison = telephone_livraison[:20]
        panier.instruction_livraison = (instruction_livraison or '').strip() or None
    else:
        panier.livraison_pays = None
        panier.livraison_ville = None
        panier.livraison_commune = None
        panier.adresse_domicile = None
        panier.telephone_livraison = None
        panier.instruction_livraison = None

    panier.frais_livraison = frais
    if isinstance(moyen_paiement, MoyenPaiement):
        moyen = moyen_paiement
    else:
        moyen = moyen_depuis_code(moyen_paiement)
    panier.mode_reception = mode_reception
    panier.valide = True
    panier.commande_en_ligne = True
    panier.nom_client = user.get_full_name() or user.username
    panier.save()

    total = sous_total + frais
    commande = Commande.objects.create(
        panier=panier,
        numero_commande=f'WEB-{panier.id}-{Commande.objects.filter(panier=panier).count() + 1}',
        total=total,
        total_sans_remise=sous_total,
        remise=Decimal('0'),
        paye=False,
        utilisateur=user,
        commande_en_ligne=True,
        moyen_paiement=moyen,
        statut_commande='en_attente',
    )
    ticket = Ticket.objects.create(
        numero=f'TKT{commande.id}',
        commande=commande,
        utilise=False,
        client_name=(user.get_full_name() or user.username)[:25],
        utilisateur=user,
    )
    panier.ticket = ticket.numero
    panier.save(update_fields=[
        'ticket', 'mode_reception', 'valide', 'commande_en_ligne', 'nom_client',
        'livraison_pays', 'livraison_ville', 'livraison_commune',
        'adresse_domicile', 'telephone_livraison', 'frais_livraison', 'instruction_livraison',
    ])

    notifier_commande_en_ligne(commande)
    _publish_cmd_ligne_mqtt(commande, 'nouvelle')
    return commande, ticket


@transaction.atomic
def valider_commande_magasin(commande: Commande, staff_user) -> Commande:
    if commande.statut_commande == 'annuler':
        raise ValueError('Commande annulée : validation impossible.')
    if not commande.commande_en_ligne or commande.statut_commande != 'en_attente':
        raise ValueError('Commande non eligible à la validation.')
    commande.statut_commande = 'valider'
    commande.utilisateur = staff_user
    commande.save(update_fields=['statut_commande', 'utilisateur'])
    _publish_cmd_ligne_mqtt(commande, 'valider')
    return commande


@transaction.atomic
def assigner_livreur(commande: Commande, livreur: CustomUser) -> Commande:
    if livreur.role != 'livreur':
        raise ValueError('Utilisateur non livreur.')
    local = commande.panier.local_entrepot
    if livreur.local_entrepot_id != local.pk:
        raise ValueError('Ce livreur n\'est pas affecté à cette localité.')
    if commande.statut_commande == 'annuler':
        raise ValueError('Commande annulée : assignation impossible.')
    if commande.statut_commande not in ('valider', 'en_attente'):
        raise ValueError('Commande non eligible.')

    was_same_livreur = commande.livreur_id == livreur.pk
    commande.livreur = livreur
    if commande.statut_commande == 'en_attente':
        commande.statut_commande = 'valider'
    commande.save(update_fields=['livreur', 'statut_commande'])

    # Notification à chaque (ré)assignation vers un livreur différent
    if not was_same_livreur:
        Notification.objects.create(
            type_notification='commande_en_ligne',
            titre='Nouvelle commande à livrer',
            message=(
                f'Commande {commande.numero_commande} — '
                f'{commande.total:,.0f} FCFA — '
                f'{commande.panier.get_mode_reception_display()} — '
                f'{commande.panier.local_entrepot.nom if commande.panier.local_entrepot else "—"}.'
            ),
            utilisateur=livreur,
        )
    _publish_cmd_ligne_mqtt(commande, 'assigner')
    return commande


@transaction.atomic
def confirmer_paiement_en_ligne(
    commande: Commande,
    moyen=None,
    montant_paye=None,
    staff_user=None,
    *,
    silent_if_paid: bool = True,
) -> Commande:
    """Marque une commande e-com comme payée et décrémente le stock (idempotent)."""
    from django.core.exceptions import ValidationError

    commande = (
        # of='self' : `moyen_paiement` et `panier__local_entrepot` nullables,
        # donc jointure externe, que PostgreSQL refuse de verrouiller.
        Commande.objects.select_for_update(of=('self',))
        .select_related('panier', 'panier__local_entrepot', 'moyen_paiement')
        .get(pk=commande.pk)
    )
    if commande.paye:
        if silent_if_paid:
            return commande
        raise ValueError('Commande déjà payée.')
    if commande.statut_commande == 'annuler':
        raise ValueError('Commande annulée.')

    panier = Panier.objects.select_for_update().get(pk=commande.panier_id)
    local = panier.local_entrepot
    if not local:
        raise ValueError('Localité de la commande introuvable.')

    items = list(PanierItem.objects.filter(panier=panier).select_related('piece'))
    if not items:
        raise ValueError('Aucun article dans cette commande.')

    try:
        for item in items:
            decrementer_stock(item.piece, local, item.quantite)
    except ValidationError as exc:
        raise ValueError(
            '; '.join(exc.messages) if getattr(exc, 'messages', None) else str(exc)
        ) from exc

    moyen = moyen or commande.moyen_paiement or get_moyen_geniuspay()
    paye = montant_paye if montant_paye is not None else commande.total
    commande.paye = True
    commande.moyen_paiement = moyen
    commande.montant_paye = paye
    commande.montant_reste = Decimal('0')
    if commande.statut_commande == 'en_attente':
        commande.statut_commande = 'valider'
    if staff_user is not None:
        ticket_user = staff_user
    else:
        ticket_user = commande.utilisateur
    commande.save()

    panier.panier_paye = True
    panier.date_paie_panier = timezone.now().date()
    if not panier.commande_en_ligne:
        panier.commande_en_ligne = True
    panier.save(update_fields=['panier_paye', 'date_paie_panier', 'commande_en_ligne'])

    ticket = Ticket.objects.filter(commande=commande).first()
    if ticket:
        ticket.utilise = True
        if ticket_user is not None:
            ticket.utilisateur = ticket_user
            ticket.save(update_fields=['utilise', 'utilisateur'])
        else:
            ticket.save(update_fields=['utilise'])
    _publish_cmd_ligne_mqtt(commande, 'payer')
    return commande


@transaction.atomic
def payer_commande_livreur(commande: Commande, livreur: CustomUser) -> Commande:
    if commande.livreur_id != livreur.pk:
        raise ValueError('Cette commande ne vous est pas assignée.')
    if commande.paye:
        raise ValueError('Commande déjà payée.')
    if commande.statut_commande == 'annuler':
        raise ValueError('Commande annulée.')

    moyen = commande.moyen_paiement or get_moyen_espece()
    return confirmer_paiement_en_ligne(
        commande,
        moyen=moyen,
        staff_user=livreur,
        silent_if_paid=False,
    )


@transaction.atomic
def livrer_commande_livreur(commande: Commande, livreur: CustomUser) -> Commande:
    if commande.livreur_id != livreur.pk:
        raise ValueError('Cette commande ne vous est pas assignée.')
    if not commande.paye:
        raise ValueError('Le paiement doit être enregistré avant la livraison.')
    if commande.statut_commande == 'annuler':
        raise ValueError('Commande annulée.')
    if commande.statut_commande == 'livrer' and commande.panier.panier_livre:
        return commande

    commande = (
        Commande.objects.select_for_update()
        .select_related('panier')
        .get(pk=commande.pk)
    )
    panier = Panier.objects.select_for_update().get(pk=commande.panier_id)
    today = timezone.now().date()

    commande.statut_commande = 'livrer'
    commande.save(update_fields=['statut_commande'])

    panier.panier_livre = True
    panier.date_livr_panier = today
    if not panier.commande_en_ligne:
        panier.commande_en_ligne = True
    # Sauvegarde complète (pas seulement update_fields) pour bien persister
    # panier_livre / date_livr_panier avec django-simple-history.
    panier.save()

    # Filet de sécurité : forcer l'UPDATE SQL si le save modèle était partiel.
    Panier.objects.filter(pk=panier.pk).update(
        panier_livre=True,
        date_livr_panier=today,
        commande_en_ligne=True,
    )
    panier.refresh_from_db(fields=['panier_livre', 'date_livr_panier', 'commande_en_ligne'])
    if not panier.panier_livre:
        raise ValueError('Impossible de marquer le panier comme livré.')
    _publish_cmd_ligne_mqtt(commande, 'livrer')
    return commande


@transaction.atomic
def confirmer_reception_client(commande: Commande, client: CustomUser) -> Commande:
    if commande.panier.utilisateur_id != client.pk:
        raise ValueError('Commande introuvable.')
    if commande.statut_commande != 'livrer':
        raise ValueError('La commande n\'a pas encore été livrée.')
    commande.client_confirme_reception = True
    commande.date_reception_client = timezone.now()
    commande.save(update_fields=['client_confirme_reception', 'date_reception_client'])
    return commande


@transaction.atomic
def annuler_commande_client(commande: Commande, client: CustomUser) -> Commande:
    """
    Annulation par le client tant que la commande n'est ni payée ni livrée.
    Possible même après assignation d'un livreur (statut valider).
    Pas de restauration de stock : le stock n'est décrémenté qu'au paiement livreur.
    """
    if not commande.commande_en_ligne:
        raise ValueError('Commande introuvable.')
    if commande.panier.utilisateur_id != client.pk:
        raise ValueError('Commande introuvable.')
    if commande.statut_commande == 'annuler':
        raise ValueError('Cette commande est déjà annulée.')
    if commande.statut_commande == 'livrer' or commande.panier.panier_livre:
        raise ValueError('Impossible d\'annuler une commande déjà livrée.')
    if commande.paye or commande.panier.panier_paye:
        raise ValueError('Impossible d\'annuler une commande déjà payée.')
    if commande.statut_commande not in ('en_attente', 'valider'):
        raise ValueError('Cette commande ne peut plus être annulée.')

    commande.statut_commande = 'annuler'
    commande.save(update_fields=['statut_commande'])

    # Prévenir le livreur assigné s'il y en a un
    if commande.livreur_id:
        Notification.objects.create(
            type_notification='commande_en_ligne',
            titre='Commande annulée par le client',
            message=(
                f'La commande {commande.numero_commande} a été annulée par le client. '
                f'Ne pas procéder au paiement ni à la livraison.'
            ),
            utilisateur=commande.livreur,
        )
    _publish_cmd_ligne_mqtt(commande, 'annuler')
    return commande


MAX_VUES_RECENTES_INDEX = 8
MAX_VUES_RECENTES_PREVIEW = 6
MAX_VUES_RECENTES_TOTAL = 50

MAX_RECHERCHES_RECENTES = 8
MAX_RECHERCHES_RECENTES_TOTAL = 20
MAX_RECHERCHES_POPULAIRES = 10
MIN_RECHERCHE_LEN = 2


def normaliser_terme_recherche(raw: str) -> tuple[str, str]:
    """Retourne (terme_normalise, terme_affiche) ou ('', '') si invalide."""
    affiche = ' '.join((raw or '').strip().split())
    if len(affiche) < MIN_RECHERCHE_LEN:
        return '', ''
    if len(affiche) > 150:
        affiche = affiche[:150].rstrip()
    return affiche.casefold(), affiche


def _session_key_recherche(request) -> str:
    if not request.session.session_key:
        request.session.create()
    return request.session.session_key or ''


def enregistrer_recherche(request, query: str) -> None:
    """Enregistre une recherche (historique client + popularité globale)."""
    from django.db.models import F

    from .models import RecherchePopulaire, RechercheRecente

    terme, affiche = normaliser_terme_recherche(query)
    if not terme:
        return

    populaire, created = RecherchePopulaire.objects.get_or_create(
        terme=terme,
        defaults={'terme_affiche': affiche, 'compteur': 1},
    )
    if not created:
        RecherchePopulaire.objects.filter(pk=populaire.pk).update(
            compteur=F('compteur') + 1,
            terme_affiche=affiche,
        )

    user = request.user if getattr(request.user, 'is_authenticated', False) else None
    if user:
        obj, created = RechercheRecente.objects.update_or_create(
            utilisateur=user,
            terme=terme,
            defaults={'terme_affiche': affiche, 'session_key': ''},
        )
        if not created:
            obj.terme_affiche = affiche
            obj.save(update_fields=['terme_affiche', 'date_recherche'])
        ids_a_garder = list(
            RechercheRecente.objects.filter(utilisateur=user)
            .order_by('-date_recherche')
            .values_list('pk', flat=True)[:MAX_RECHERCHES_RECENTES_TOTAL]
        )
        if ids_a_garder:
            RechercheRecente.objects.filter(utilisateur=user).exclude(pk__in=ids_a_garder).delete()
    else:
        session_key = _session_key_recherche(request)
        if not session_key:
            return
        obj, created = RechercheRecente.objects.update_or_create(
            session_key=session_key,
            utilisateur=None,
            terme=terme,
            defaults={'terme_affiche': affiche},
        )
        if not created:
            obj.terme_affiche = affiche
            obj.save(update_fields=['terme_affiche', 'date_recherche'])
        ids_a_garder = list(
            RechercheRecente.objects.filter(session_key=session_key, utilisateur__isnull=True)
            .order_by('-date_recherche')
            .values_list('pk', flat=True)[:MAX_RECHERCHES_RECENTES_TOTAL]
        )
        if ids_a_garder:
            RechercheRecente.objects.filter(
                session_key=session_key, utilisateur__isnull=True
            ).exclude(pk__in=ids_a_garder).delete()


def queryset_recherches_recentes(request):
    from .models import RechercheRecente

    user = request.user if getattr(request.user, 'is_authenticated', False) else None
    if user:
        return RechercheRecente.objects.filter(utilisateur=user).order_by('-date_recherche')
    session_key = getattr(request.session, 'session_key', None) or ''
    if not session_key:
        return RechercheRecente.objects.none()
    return RechercheRecente.objects.filter(
        session_key=session_key, utilisateur__isnull=True
    ).order_by('-date_recherche')


def liste_recherches_recentes(request, limit: int = MAX_RECHERCHES_RECENTES) -> list[str]:
    return list(
        queryset_recherches_recentes(request).values_list('terme_affiche', flat=True)[:limit]
    )


def liste_recherches_populaires(limit: int = MAX_RECHERCHES_POPULAIRES) -> list[str]:
    from .models import RecherchePopulaire

    return list(
        RecherchePopulaire.objects.order_by('-compteur', '-derniere_recherche')
        .values_list('terme_affiche', flat=True)[:limit]
    )


def effacer_recherches_recentes(request) -> int:
    from .models import RechercheRecente

    qs = queryset_recherches_recentes(request)
    count, _ = qs.delete()
    return count


def enregistrer_vue_recente(user, piece: Piece) -> None:
    """Enregistre / met à jour une consultation de pièce pour l'utilisateur."""
    from .models import VueRecentePiece

    if not user or not user.is_authenticated:
        return
    VueRecentePiece.objects.update_or_create(
        utilisateur=user,
        piece=piece,
        defaults={},
    )
    # Limite l'historique
    ids_a_garder = list(
        VueRecentePiece.objects.filter(utilisateur=user)
        .order_by('-date_vue')
        .values_list('pk', flat=True)[:MAX_VUES_RECENTES_TOTAL]
    )
    if ids_a_garder:
        VueRecentePiece.objects.filter(utilisateur=user).exclude(pk__in=ids_a_garder).delete()


def queryset_vues_recentes(user):
    from .models import VueRecentePiece

    if not user or not user.is_authenticated:
        return VueRecentePiece.objects.none()
    return (
        VueRecentePiece.objects.filter(utilisateur=user)
        .select_related('piece', 'piece__categorie', 'piece__sous_categorie')
        .order_by('-date_vue')
    )


def lignes_vues_recentes(user, local=None, *, limit: int | None = MAX_VUES_RECENTES_PREVIEW):
    """Lignes d'affichage pour la section Vus récemment."""
    qs = queryset_vues_recentes(user)
    if limit:
        qs = qs[:limit]
    rows = []
    for vue in qs:
        piece = vue.piece
        prix = get_prix_unitaire(piece, local) if local else piece.prix_unitaire
        quantite = quantite_disponible_piece(piece, local)
        localite_code = str(local.code) if local else ''
        localite_nom = local.nom if local else ''
        enrichir_piece_stock(piece, local)
        rows.append({
            'vue': vue,
            'piece': piece,
            'prix_affiche': prix,
            'date_vue': vue.date_vue,
            'quantite': quantite,
            'localite_code': localite_code,
            'localite_nom': localite_nom,
        })
    return rows


def lignes_favoris(user, local=None):
    """Lignes d'affichage favoris avec stock / prix cohérents."""
    from .models import FavoriPiece

    favoris = (
        FavoriPiece.objects.filter(utilisateur=user)
        .select_related('piece', 'piece__categorie', 'piece__sous_categorie')
        .order_by('-date_ajout')
    )
    rows = []
    for fav in favoris:
        piece = fav.piece
        prix = get_prix_unitaire(piece, local) if local else piece.prix_unitaire
        quantite = quantite_disponible_piece(piece, local)
        enrichir_piece_stock(piece, local)
        rows.append({
            'favori': fav,
            'piece': piece,
            'prix_affiche': prix,
            'quantite': quantite,
            'localite_code': str(local.code) if local else '',
            'localite_nom': local.nom if local else '',
        })
    return rows
