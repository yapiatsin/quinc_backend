"""
Stock par localité : couche métier (StockLocal = source de vérité).
"""
from __future__ import annotations

from decimal import Decimal

from django.db import transaction
from django.db.models import F, Sum, OuterRef, Subquery, Value, IntegerField, CharField, DecimalField, Q, BooleanField
from django.db.models.functions import Coalesce
from django.core.exceptions import ValidationError

from Userauths.models import LocalEntrepot
from .models import Piece, StockLocal


def filter_piece_catalogue_actif(queryset):
    """Pièces non archivées au niveau catalogue."""
    return queryset.filter(Q(active_sortie=True) | Q(active_sortie__isnull=True))


def filter_categories_actives(queryset):
    """Catégories visibles boutique / vente."""
    return queryset.filter(actif=True)


def filter_pieces_categories_actives(queryset):
    """Pièces dont la catégorie (et sous-catégorie le cas échéant) est active."""
    return queryset.filter(categorie__actif=True).filter(
        Q(sous_categorie__isnull=True) | Q(sous_categorie__actif=True)
    )


def annotate_archive_info(queryset, local_entrepot: LocalEntrepot | None):
    """Annoter les infos d'archivage (utilisateur / localité) pour l'affichage."""
    qs = queryset.select_related('archive_par')
    if local_entrepot is None:
        return qs
    subq = StockLocal.objects.filter(
        piece_id=OuterRef('pk'),
        local_entrepot=local_entrepot,
    )
    return qs.annotate(
        archive_par_local_username=Subquery(
            subq.values('archive_par__username')[:1],
            output_field=CharField(),
        ),
        archive_le_local=Subquery(subq.values('archive_le')[:1]),
        archive_motif_local=Subquery(subq.values('archive_motif')[:1], output_field=CharField()),
        archive_local_nom=Subquery(
            subq.values('local_entrepot__nom')[:1],
            output_field=CharField(),
        ),
    )


def filter_pieces_archivees(queryset, local_entrepot: LocalEntrepot | None):
    """Pièces archivées catalogue et/ou désactivées dans la localité."""
    if local_entrepot is None:
        return queryset.filter(active_sortie=False)
    return queryset.filter(
        Q(active_sortie=False)
        | Q(stocks__local_entrepot=local_entrepot, stocks__active_sortie=False),
    ).distinct()


def piece_catalogue_actif(piece: Piece) -> bool:
    return piece.active_sortie is not False


def piece_active_sortie_local(piece: Piece, local_entrepot: LocalEntrepot | None) -> bool:
    """active_sortie de la ligne StockLocal (True si aucune ligne)."""
    if local_entrepot is None:
        return True
    stock = get_stock(piece, local_entrepot)
    if stock is None:
        return True
    return stock.active_sortie


def piece_visible_en_localite(piece: Piece, local_entrepot: LocalEntrepot | None) -> bool:
    """Visible à la vente : catalogue actif ET catégorie active ET stock local actif."""
    if not piece_catalogue_actif(piece):
        return False
    if piece.categorie_id and not getattr(piece.categorie, 'actif', True):
        return False
    if piece.sous_categorie_id and not getattr(piece.sous_categorie, 'actif', True):
        return False
    if local_entrepot is None:
        return True
    stock = get_stock(piece, local_entrepot)
    if stock is None:
        return False
    return stock.active_sortie


def get_prix_unitaire(piece: Piece, local_entrepot: LocalEntrepot | None, new_price=None):
    """
    Prix unitaire appliqué : remise manuelle (new_price) > prix local > prix catalogue.
    """
    if new_price is not None and Decimal(str(new_price)) > 0:
        return Decimal(str(new_price))
    if local_entrepot is not None:
        stock = get_stock(piece, local_entrepot)
        if stock and stock.prix_unitaire_local is not None:
            return stock.prix_unitaire_local
    return piece.prix_unitaire


def total_panier_items(items, local_entrepot=None, *, panier=None):
    """Total panier en tenant compte du prix par localité."""
    if local_entrepot is None and panier is not None:
        local_entrepot = panier.local_entrepot
    total = Decimal('0')
    for item in items:
        loc = local_entrepot
        if loc is None and getattr(item, 'panier', None):
            loc = item.panier.local_entrepot
        total += get_prix_unitaire(item.piece, loc, item.new_price) * item.quantite
    return total


def get_quantite(piece: Piece, local_entrepot: LocalEntrepot | None) -> int:
    """Quantité disponible pour une pièce dans une localité (0 si aucune ligne)."""
    if local_entrepot is None:
        return piece.stocks.aggregate(t=Sum('quantite_disponible'))['t'] or 0
    stock = piece.stocks.filter(local_entrepot=local_entrepot).first()
    return stock.quantite_disponible if stock else 0


def get_quantite_vendable(piece: Piece, local_entrepot: LocalEntrepot | None) -> int:
    """
    Quantité vendable (catalogue ecom / vente) :
    uniquement les lignes StockLocal avec active_sortie=True.
    """
    qs = piece.stocks.filter(active_sortie=True)
    if local_entrepot is None:
        return qs.aggregate(t=Sum('quantite_disponible'))['t'] or 0
    stock = qs.filter(local_entrepot=local_entrepot).first()
    return int(stock.quantite_disponible) if stock else 0


def get_stock(piece: Piece, local_entrepot: LocalEntrepot, *, for_update: bool = False):
    """Retourne la ligne StockLocal (sans la créer)."""
    qs = StockLocal.objects.filter(piece=piece, local_entrepot=local_entrepot)
    if for_update:
        qs = qs.select_for_update()
    return qs.first()


@transaction.atomic
def get_or_create_stock(
    piece: Piece,
    local_entrepot: LocalEntrepot,
    *,
    seuil_local: int | None = None,
    emplacement: str = '',
    for_update: bool = False,
) -> StockLocal:
    defaults = {
        'quantite_disponible': 0,
        'seuil_local': seuil_local if seuil_local is not None else piece.seuil,
        'emplacement': emplacement or piece.emplacement or '',
        'active_sortie': piece_catalogue_actif(piece),
    }
    if for_update:
        stock, _ = StockLocal.objects.select_for_update().get_or_create(
            piece=piece,
            local_entrepot=local_entrepot,
            defaults=defaults,
        )
        return stock
    stock, _ = StockLocal.objects.get_or_create(
        piece=piece,
        local_entrepot=local_entrepot,
        defaults=defaults,
    )
    return stock


@transaction.atomic
def incrementer_stock(
    piece: Piece,
    local_entrepot: LocalEntrepot,
    quantite: int,
    *,
    seuil_local: int | None = None,
) -> StockLocal:
    if quantite <= 0:
        raise ValidationError("La quantité à ajouter doit être positive.")
    stock = get_or_create_stock(
        piece, local_entrepot, seuil_local=seuil_local, for_update=True
    )
    stock.quantite_disponible += quantite
    stock.save(update_fields=['quantite_disponible', 'date_maj'])
    return stock


@transaction.atomic
def decrementer_stock(
    piece: Piece,
    local_entrepot: LocalEntrepot,
    quantite: int,
) -> StockLocal:
    if quantite <= 0:
        raise ValidationError("La quantité à retirer doit être positive.")
    stock = get_or_create_stock(piece, local_entrepot, for_update=True)
    if stock.quantite_disponible < quantite:
        raise ValidationError(
            f"Stock insuffisant à {local_entrepot.nom} pour « {piece.designation} » : "
            f"{stock.quantite_disponible} disponible, {quantite} demandé."
        )
    stock.quantite_disponible -= quantite
    stock.save(update_fields=['quantite_disponible', 'date_maj'])
    return stock


@transaction.atomic
def ajuster_stock_entree(piece: Piece, local_entrepot: LocalEntrepot, ancienne_qte: int, nouvelle_qte: int):
    """Ajustement lors de la modification d'une EntrePiece."""
    diff = nouvelle_qte - ancienne_qte
    if diff > 0:
        incrementer_stock(piece, local_entrepot, diff)
    elif diff < 0:
        decrementer_stock(piece, local_entrepot, -diff)


def annotate_provenance_derniere_entree(queryset, local_entrepot: LocalEntrepot | None):
    """Dernière entrée en stock : type de provenance (fournisseur / autre localité)."""
    from .models import EntrePiece
    if local_entrepot is None:
        return queryset.annotate(
            derniere_origine_type=Value('fournisseur', output_field=CharField()),
            derniere_origine_nom=Value('', output_field=CharField()),
        )
    subq = EntrePiece.objects.filter(
        piece_id=OuterRef('pk'),
        local_entrepot=local_entrepot,
    ).order_by('-date')
    return queryset.annotate(
        derniere_origine_type=Coalesce(
            Subquery(subq.values('origine_type')[:1]),
            Value('fournisseur'),
            output_field=CharField(),
        ),
        derniere_origine_nom=Coalesce(
            Subquery(subq.values('origine_local__nom')[:1]),
            Value(''),
            output_field=CharField(),
        ),
    )


def annotate_pieces_for_localite(queryset, local_entrepot: LocalEntrepot | None):
    """
    Ajoute quantite_disponible (et seuil_affiche) sur chaque Piece pour l'affichage.
    """
    if local_entrepot is None:
        return queryset.annotate(
            quantite_disponible=Coalesce(Sum('stocks__quantite_disponible'), 0),
            seuil_affiche=F('seuil'),
        )
    subq = StockLocal.objects.filter(
        piece_id=OuterRef('pk'),
        local_entrepot=local_entrepot,
    )
    return queryset.annotate(
        quantite_disponible=Coalesce(
            Subquery(subq.values('quantite_disponible')[:1]),
            Value(0),
            output_field=IntegerField(),
        ),
        seuil_affiche=Coalesce(
            Subquery(subq.values('seuil_local')[:1]),
            F('seuil'),
        ),
        stock_local_id=Subquery(subq.values('pk')[:1]),
        prix_unitaire_local_valeur=Subquery(subq.values('prix_unitaire_local')[:1]),
        prix_affiche=Coalesce(
            Subquery(subq.values('prix_unitaire_local')[:1]),
            F('prix_unitaire'),
            output_field=DecimalField(max_digits=10, decimal_places=2),
        ),
        active_sortie_local=Coalesce(
            Subquery(subq.values('active_sortie')[:1]),
            Value(True),
            output_field=BooleanField(),
        ),
    )


def filter_pieces_avec_stock(queryset, local_entrepot: LocalEntrepot | None, *, min_qte: int = 1):
    """Pièces ayant du stock dans la localité (catalogue + sortie locale actifs)."""
    qs = filter_piece_catalogue_actif(queryset)
    if local_entrepot is None:
        return qs.filter(stocks__quantite_disponible__gte=min_qte).distinct()
    return qs.filter(
        stocks__local_entrepot=local_entrepot,
        stocks__quantite_disponible__gte=min_qte,
        stocks__active_sortie=True,
    ).distinct()


def filter_pieces_sous_seuil(queryset, local_entrepot: LocalEntrepot | None):
    """Pièces en alerte selon le seuil local ou catalogue."""
    qs = filter_piece_catalogue_actif(queryset.filter(seuil__gt=0))
    qs = annotate_pieces_for_localite(qs, local_entrepot)
    if local_entrepot is not None:
        qs = qs.filter(
            stocks__local_entrepot=local_entrepot,
            stocks__active_sortie=True,
        ).distinct()
    return qs.filter(quantite_disponible__lt=F('seuil_affiche'))


def piece_color_status(quantite: int, seuil: int) -> str:
    if quantite <= 0:
        return 'danger'
    if seuil > 0 and quantite < seuil / 2:
        return 'danger'
    if seuil > 0 and quantite < seuil:
        return 'warning'
    return 'success'
