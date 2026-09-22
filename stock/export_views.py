"""Vues d'export Excel / PDF pour les listes magasin (respectent les filtres GET)."""
from datetime import date
from decimal import Decimal

from django.contrib.auth.decorators import login_required
from django.db.models import Count, DecimalField, ExpressionWrapper, F, FloatField, Sum
from django.db.models.functions import Coalesce
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, redirect
from django.views.decorators.http import require_GET

from stock.export_service import cell, excel_response, pdf_response
from stock.models import (
    BonCommandePaiement,
    Categorie,
    Commande,
    EntrePiece,
    Panier,
    PanierItem,
    Piece,
    Ticket,
)
from stock.period_filters import periode_filter_context
from stock.stock_local_service import (
    annotate_pieces_for_localite,
    annotate_provenance_derniere_entree,
    filter_piece_catalogue_actif,
)


def _fmt_money(value):
    if value is None:
        return '0'
    try:
        return f'{Decimal(value):.2f}'
    except Exception:
        return str(value)


def _fmt_bool_paye(paye):
    return 'Payé' if paye else 'Impayé'


def _commande_rows(cmdes):
    headers = [
        'N°#', 'N°Commande', 'Montant', 'Remise', 'Total', 'Versé', 'Reste',
        'Paiement', 'N°ticket', 'Caissier', 'Date',
    ]
    rows = []
    for i, c in enumerate(cmdes, start=1):
        ticket_num = ''
        try:
            ticket_num = c.ticket.numero if c.ticket else ''
        except Exception:
            ticket_num = ''
        rows.append([
            i,
            cell(c.numero_commande),
            _fmt_money(c.total_sans_remise),
            _fmt_money(c.remise),
            _fmt_money(c.total),
            _fmt_money(c.montant_paye),
            _fmt_money(c.montant_reste),
            _fmt_bool_paye(c.paye),
            cell(ticket_num),
            cell(getattr(c.utilisateur, 'username', None)),
            c.date_creation.strftime('%d-%m-%Y') if c.date_creation else '',
        ])
    return headers, rows


# ── Liste ventes ──────────────────────────────────────────────────────────

def _qs_liste_ventes(request):
    filt = periode_filter_context(
        request, request.user, reset_url_name='liste_ventes', default='aujourdhui',
    )
    localite = filt['localite_active']
    qs = Commande.objects.filter(paye=True, panier__valide=True)
    if localite:
        qs = qs.filter(panier__local_entrepot=localite)
    qs = (
        qs.filter(date_creation__range=[filt['date_debut'], filt['date_fin']])
        .select_related('utilisateur', 'ticket')
        .order_by('-date_creation', '-date')
    )
    return qs, filt


@login_required(login_url='connexion')
@require_GET
def export_liste_ventes_excel(request):
    cmdes, filt = _qs_liste_ventes(request)
    headers, rows = _commande_rows(cmdes)
    return excel_response(
        f"ventes_{filt['date_debut']}_{filt['date_fin']}.xlsx",
        headers, rows, sheet_title='Ventes',
    )


@login_required(login_url='connexion')
@require_GET
def export_liste_ventes_pdf(request):
    cmdes, filt = _qs_liste_ventes(request)
    headers, rows = _commande_rows(cmdes)
    return pdf_response(
        request,
        title='Liste des ventes',
        subtitle=filt.get('filtre_resume', ''),
        headers=headers,
        rows=rows,
        filename=f"ventes_{filt['date_debut']}_{filt['date_fin']}.pdf",
    )


# ── Liste commandes ───────────────────────────────────────────────────────

def _qs_liste_commandes(request):
    filt = periode_filter_context(
        request, request.user, reset_url_name='liste_commandes',
    )
    localite = filt['localite_active']
    qs = Commande.objects.filter(
        date_creation__range=[filt['date_debut'], filt['date_fin']]
    ).select_related('utilisateur', 'ticket').order_by('-date_creation')
    if localite:
        qs = qs.filter(panier__local_entrepot=localite)
    return qs, filt


@login_required(login_url='connexion')
@require_GET
def export_liste_commandes_excel(request):
    cmdes, filt = _qs_liste_commandes(request)
    headers, rows = _commande_rows(cmdes)
    return excel_response(
        f"commandes_{filt['date_debut']}_{filt['date_fin']}.xlsx",
        headers, rows, sheet_title='Commandes',
    )


@login_required(login_url='connexion')
@require_GET
def export_liste_commandes_pdf(request):
    cmdes, filt = _qs_liste_commandes(request)
    headers, rows = _commande_rows(cmdes)
    return pdf_response(
        request,
        title='Liste des commandes',
        subtitle=filt.get('filtre_resume', ''),
        headers=headers,
        rows=rows,
        filename=f"commandes_{filt['date_debut']}_{filt['date_fin']}.pdf",
    )


# ── Stock ─────────────────────────────────────────────────────────────────

def _qs_stock(request):
    filt = periode_filter_context(request, request.user, reset_url_name='stock')
    localite = filt['localite_active']
    qs = filter_piece_catalogue_actif(Piece.objects.all())
    if localite:
        qs = qs.filter(
            stocks__local_entrepot=localite,
            stocks__active_sortie=True,
        ).distinct()
    qs = qs.select_related('categorie', 'sous_categorie', 'utilisateur').order_by('-date_creation')
    qs = annotate_pieces_for_localite(qs, localite)
    qs = annotate_provenance_derniere_entree(qs, localite)
    return qs, filt


@login_required(login_url='connexion')
@require_GET
def export_stock_excel(request):
    pieces, filt = _qs_stock(request)
    headers = [
        'N°#', 'N°Pièce', 'Désignation', 'Catégorie', 'Sous-catégorie', 'Prix achat',
        'Prix vente', 'Prix vente local', 'Qté stock', 'Provenance',
        'Seuil', 'Emplacement', 'Auteur', 'Date',
    ]
    rows = []
    for i, p in enumerate(pieces, start=1):
        prov = (
            f"↪ {p.derniere_origine_nom}"
            if getattr(p, 'derniere_origine_type', None) == 'transfert_local'
            and getattr(p, 'derniere_origine_nom', None)
            else 'Fournisseur'
        )
        prix_local = getattr(p, 'prix_unitaire_local_valeur', None)
        rows.append([
            i,
            cell(p.numero_piece),
            cell(p.designation),
            cell(getattr(p.categorie, 'categorie', None)),
            cell(getattr(p.sous_categorie, 'nom', None)),
            _fmt_money(p.prix_achat),
            _fmt_money(p.prix_unitaire),
            _fmt_money(prix_local) if prix_local is not None else '—',
            getattr(p, 'quantite_disponible', 0),
            prov,
            p.seuil,
            cell(p.emplacement),
            cell(getattr(p.utilisateur, 'username', None)),
            p.date_creation.strftime('%d-%m-%Y') if p.date_creation else '',
        ])
    return excel_response(
        f"stock_{filt['date_debut']}_{filt['date_fin']}.xlsx",
        headers, rows, sheet_title='Stock',
    )


@login_required(login_url='connexion')
@require_GET
def export_stock_pdf(request):
    pieces, filt = _qs_stock(request)
    headers = [
        'N°#', 'N°Pièce', 'Désignation', 'Catégorie', 'Sous-cat.', 'Prix vente',
        'Qté', 'Seuil', 'Emplacement', 'Date',
    ]
    rows = []
    for i, p in enumerate(pieces, start=1):
        rows.append([
            i,
            cell(p.numero_piece),
            cell(p.designation),
            cell(getattr(p.categorie, 'categorie', None)),
            cell(getattr(p.sous_categorie, 'nom', None)),
            _fmt_money(p.prix_unitaire),
            getattr(p, 'quantite_disponible', 0),
            p.seuil,
            cell(p.emplacement),
            p.date_creation.strftime('%d-%m-%Y') if p.date_creation else '',
        ])
    return pdf_response(
        request,
        title='Stock de pièces',
        subtitle=filt.get('filtre_resume', ''),
        headers=headers,
        rows=rows,
        filename=f"stock_{filt['date_debut']}_{filt['date_fin']}.pdf",
    )


# ── Caisse (ventes du jour) ───────────────────────────────────────────────

def _qs_caisse_ventes(request):
    from stock.views import get_user_localite
    today = date.today()
    localite = get_user_localite(request.user)
    # Aligné sur Caisse() : ventes du jour ; localité via filtre GET si fourni
    filt = periode_filter_context(
        request, request.user, reset_url_name='caissiere', default='aujourdhui',
    )
    localite = filt['localite_active'] or localite
    qs = (
        Commande.objects.filter(
            date_creation__range=[filt['date_debut'], filt['date_fin']],
            commande_en_ligne=False,
        )
        .select_related('utilisateur', 'ticket')
        .order_by('-date_creation')
    )
    if localite:
        qs = qs.filter(panier__local_entrepot=localite)
    return qs, filt


@login_required(login_url='connexion')
@require_GET
def export_caisse_ventes_excel(request):
    cmdes, filt = _qs_caisse_ventes(request)
    headers = [
        'N°#', 'N°Commande', 'Montant', 'Total', 'Remise', 'TVA',
        'Versé', 'Reste', 'Paiement', 'Caissier', 'Date',
    ]
    rows = []
    for i, c in enumerate(cmdes, start=1):
        rows.append([
            i,
            cell(c.numero_commande),
            _fmt_money(c.total_sans_remise),
            _fmt_money(c.total),
            _fmt_money(c.remise),
            _fmt_money(c.montant_tva) if c.montant_tva else '—',
            _fmt_money(c.montant_paye),
            _fmt_money(c.montant_reste),
            _fmt_bool_paye(c.paye),
            cell(getattr(c.utilisateur, 'username', None)),
            c.date_creation.strftime('%d-%m-%Y') if c.date_creation else '',
        ])
    return excel_response(
        f"caisse_ventes_{filt['date_debut']}.xlsx",
        headers, rows, sheet_title='Caisse',
    )


@login_required(login_url='connexion')
@require_GET
def export_caisse_ventes_pdf(request):
    cmdes, filt = _qs_caisse_ventes(request)
    headers, rows = _commande_rows(cmdes)
    return pdf_response(
        request,
        title='Ventes caisse',
        subtitle=filt.get('filtre_resume', ''),
        headers=headers,
        rows=rows,
        filename=f"caisse_ventes_{filt['date_debut']}.pdf",
    )


# ── Best ventes ───────────────────────────────────────────────────────────

def _qs_best_vente(request):
    filt = periode_filter_context(request, request.user, reset_url_name='topventes')
    localite = filt['localite_active']
    qs = PanierItem.objects.filter(panier__valide=True, panier__panier_paye=True)
    if localite:
        qs = qs.filter(panier__local_entrepot=localite)
    top = (
        qs.filter(date_creation__range=[filt['date_debut'], filt['date_fin']])
        .values(
            'piece__numero_piece',
            'piece__designation',
            'piece__categorie__categorie',
        )
        .annotate(
            quantite_vendue=Sum('quantite'),
            cout_total=Sum(ExpressionWrapper(
                F('piece__prix_unitaire') * F('quantite'),
                output_field=DecimalField(),
            )),
        )
        .order_by('-quantite_vendue')[:50]
    )
    return top, filt


@login_required(login_url='connexion')
@require_GET
def export_best_vente_excel(request):
    top, filt = _qs_best_vente(request)
    headers = ['N°#', 'N°Pièce', 'Désignation', 'Catégorie', 'Quantité', 'Total']
    rows = [
        [
            i,
            cell(d.get('piece__numero_piece')),
            cell(d.get('piece__designation')),
            cell(d.get('piece__categorie__categorie')),
            d.get('quantite_vendue') or 0,
            _fmt_money(d.get('cout_total')),
        ]
        for i, d in enumerate(top, start=1)
    ]
    return excel_response(
        f"meilleures_ventes_{filt['date_debut']}_{filt['date_fin']}.xlsx",
        headers, rows, sheet_title='Top ventes',
    )


@login_required(login_url='connexion')
@require_GET
def export_best_vente_pdf(request):
    top, filt = _qs_best_vente(request)
    headers = ['N°#', 'N°Pièce', 'Désignation', 'Catégorie', 'Quantité', 'Total']
    rows = [
        [
            i,
            cell(d.get('piece__numero_piece')),
            cell(d.get('piece__designation')),
            cell(d.get('piece__categorie__categorie')),
            d.get('quantite_vendue') or 0,
            _fmt_money(d.get('cout_total')),
        ]
        for i, d in enumerate(top, start=1)
    ]
    return pdf_response(
        request,
        title='Meilleures ventes',
        subtitle=filt.get('filtre_resume', ''),
        headers=headers,
        rows=rows,
        filename=f"meilleures_ventes_{filt['date_debut']}_{filt['date_fin']}.pdf",
    )


# ── Historique commandes ──────────────────────────────────────────────────

def _qs_hist_cmd(request):
    filt = periode_filter_context(
        request, request.user, reset_url_name='Historique_commande',
    )
    hist_qs = Commande.history.filter(
        history_date__date__range=[filt['date_debut'], filt['date_fin']],
    ).order_by('-history_date')
    localite = filt['localite_active']
    if localite:
        hist_qs = hist_qs.filter(panier__local_entrepot=localite)
    return list(hist_qs), filt


@login_required(login_url='connexion')
@require_GET
def export_hist_cmd_excel(request):
    hist_list, filt = _qs_hist_cmd(request)
    headers = [
        'N°#', 'ID', 'Numéro', 'Montant net', 'Remise', 'Total',
        'Payé', 'Montant payé', 'Reste', 'Type action', 'Utilisateur', 'Date action',
    ]
    rows = []
    for i, item in enumerate(hist_list, start=1):
        if item.history_type == '+':
            typ = 'Création'
        elif item.history_type == '~':
            typ = 'Modification'
        elif item.history_type == '-':
            typ = 'Suppression'
        else:
            typ = '—'
        user = (
            getattr(item.history_user, 'username', None)
            or getattr(getattr(item, 'utilisateur', None), 'username', None)
        )
        rows.append([
            i,
            cell(item.id),
            cell(item.numero_commande),
            _fmt_money(item.total_sans_remise),
            _fmt_money(item.remise),
            _fmt_money(item.total),
            _fmt_bool_paye(item.paye),
            _fmt_money(item.montant_paye),
            _fmt_money(item.montant_reste),
            typ,
            cell(user),
            item.history_date.strftime('%d/%m/%Y %H:%M') if item.history_date else '',
        ])
    return excel_response(
        f"hist_commandes_{filt['date_debut']}_{filt['date_fin']}.xlsx",
        headers, rows, sheet_title='Hist. commandes',
    )


@login_required(login_url='connexion')
@require_GET
def export_hist_cmd_pdf(request):
    hist_list, filt = _qs_hist_cmd(request)
    headers = ['N°#', 'Numéro', 'Total', 'Payé', 'Type', 'Utilisateur', 'Date']
    rows = []
    for i, item in enumerate(hist_list, start=1):
        typ = {'+': 'Création', '~': 'Modification', '-': 'Suppression'}.get(
            item.history_type, '—'
        )
        user = (
            getattr(item.history_user, 'username', None)
            or getattr(getattr(item, 'utilisateur', None), 'username', None)
        )
        rows.append([
            i,
            cell(item.numero_commande),
            _fmt_money(item.total),
            _fmt_bool_paye(item.paye),
            typ,
            cell(user),
            item.history_date.strftime('%d/%m/%Y %H:%M') if item.history_date else '',
        ])
    return pdf_response(
        request,
        title='Historique des commandes',
        subtitle=filt.get('filtre_resume', ''),
        headers=headers,
        rows=rows,
        filename=f"hist_commandes_{filt['date_debut']}_{filt['date_fin']}.pdf",
    )


# ── Historique général ────────────────────────────────────────────────────

def _qs_hist_gen(request):
    filt = periode_filter_context(
        request, request.user, reset_url_name='global_history', default='mois',
    )
    date_debut = filt['date_debut']
    date_fin = filt['date_fin']
    history_diff = []
    for model in (Panier, PanierItem, Piece, Commande, Ticket):
        for record in model.history.filter(
            history_date__date__range=[date_debut, date_fin],
        ):
            if record.prev_record:
                diff = record.diff_against(record.prev_record)
                changed = ', '.join(diff.changed_fields) if diff.changed_fields else 'N/A'
            else:
                changed = 'N/A'
            typ = {'+': 'Création', '~': 'Modification', '-': 'Suppression'}.get(
                record.history_type, '—'
            )
            history_diff.append({
                'model': model.__name__,
                'type': typ,
                'user': getattr(getattr(record, 'utilisateur', None), 'username', None)
                or getattr(record.history_user, 'username', None)
                or 'Système',
                'changes': changed,
                'date': record.history_date,
            })
    return history_diff, filt


@login_required(login_url='connexion')
@require_GET
def export_hist_gen_excel(request):
    entries, filt = _qs_hist_gen(request)
    headers = ['N°#', 'Table', "Type d'action", 'Utilisateur', 'Changements', "Date de l'action"]
    rows = [
        [
            i,
            e['model'],
            e['type'],
            cell(e['user']),
            cell(e['changes']),
            e['date'].strftime('%d %m %Y %H:%M:%S') if e['date'] else '',
        ]
        for i, e in enumerate(entries, start=1)
    ]
    return excel_response(
        f"hist_general_{filt['date_debut']}_{filt['date_fin']}.xlsx",
        headers, rows, sheet_title='Hist. général',
    )


@login_required(login_url='connexion')
@require_GET
def export_hist_gen_pdf(request):
    entries, filt = _qs_hist_gen(request)
    headers = ['N°#', 'Table', 'Type', 'Utilisateur', 'Changements', 'Date']
    rows = [
        [
            i,
            e['model'],
            e['type'],
            cell(e['user']),
            cell(e['changes']),
            e['date'].strftime('%d/%m/%Y %H:%M') if e['date'] else '',
        ]
        for i, e in enumerate(entries, start=1)
    ]
    return pdf_response(
        request,
        title='Historique général',
        subtitle=filt.get('filtre_resume', ''),
        headers=headers,
        rows=rows,
        filename=f"hist_general_{filt['date_debut']}_{filt['date_fin']}.pdf",
    )


# ── Entrées pièce ─────────────────────────────────────────────────────────

def _qs_entrees_piece(request, pk):
    filt = periode_filter_context(
        request, request.user,
        reset_url_name='info_piece', reset_url_kwargs={'pk': pk},
    )
    piece = get_object_or_404(Piece, pk=pk)
    qs = (
        EntrePiece.objects
        .filter(piece=piece, date_creation__range=[filt['date_debut'], filt['date_fin']])
        .select_related('utilisateur', 'fournisseur', 'origine_local')
        .order_by('-date_creation')
    )
    localite = filt['localite_active']
    if localite:
        qs = qs.filter(local_entrepot=localite)
    return qs, filt, piece


@login_required(login_url='connexion')
@require_GET
def export_entrees_piece_excel(request, pk):
    entrees, filt, piece = _qs_entrees_piece(request, pk)
    headers = [
        'N°#', 'Date', 'Qté ajoutée', "Prix d'achat", 'Total',
        'Provenance', 'Fournisseur', 'Ajouté par',
    ]
    rows = []
    for i, e in enumerate(entrees, start=1):
        if e.origine_type == 'transfert_local' and e.origine_local:
            prov = f'Agence {e.origine_local.nom}'
        else:
            prov = 'Fournisseur'
        total = (e.quantitajout or 0) * (e.prix_achat or 0)
        rows.append([
            i,
            e.date_creation.strftime('%d-%m-%Y') if e.date_creation else '',
            e.quantitajout,
            _fmt_money(e.prix_achat),
            _fmt_money(total),
            prov,
            cell(getattr(e.fournisseur, 'nom', None)),
            cell(getattr(e.utilisateur, 'username', None)),
        ])
    safe = str(piece.numero_piece).replace('/', '-')
    return excel_response(
        f"entrees_{safe}_{filt['date_debut']}_{filt['date_fin']}.xlsx",
        headers, rows, sheet_title='Entrées',
    )


@login_required(login_url='connexion')
@require_GET
def export_entrees_piece_pdf(request, pk):
    entrees, filt, piece = _qs_entrees_piece(request, pk)
    headers = ['N°#', 'Date', 'Qté', 'Prix achat', 'Total', 'Provenance', 'Par']
    rows = []
    for i, e in enumerate(entrees, start=1):
        if e.origine_type == 'transfert_local' and e.origine_local:
            prov = f'Agence {e.origine_local.nom}'
        else:
            prov = 'Fournisseur'
        total = (e.quantitajout or 0) * (e.prix_achat or 0)
        rows.append([
            i,
            e.date_creation.strftime('%d-%m-%Y') if e.date_creation else '',
            e.quantitajout,
            _fmt_money(e.prix_achat),
            _fmt_money(total),
            prov,
            cell(getattr(e.utilisateur, 'username', None)),
        ])
    safe = str(piece.numero_piece).replace('/', '-')
    return pdf_response(
        request,
        title=f"Entrées — {piece.designation} ({piece.numero_piece})",
        subtitle=filt.get('filtre_resume', ''),
        headers=headers,
        rows=rows,
        filename=f"entrees_{safe}_{filt['date_debut']}_{filt['date_fin']}.pdf",
    )


# ── Pièces par catégorie ──────────────────────────────────────────────────

def _qs_pieces_categorie(request, pk):
    from stock.views import get_user_localite
    categorie = get_object_or_404(Categorie, pk=pk)
    pieces = annotate_pieces_for_localite(
        Piece.objects.filter(categorie=categorie).select_related(
            'utilisateur', 'sous_categorie'
        ).order_by('id'),
        get_user_localite(request.user),
    )
    return pieces, categorie


@login_required(login_url='connexion')
@require_GET
def export_pieces_categorie_excel(request, pk):
    pieces, categorie = _qs_pieces_categorie(request, pk)
    headers = [
        'N°#', 'N°Pièce', 'Désignation', 'Sous-catégorie', 'Prix achat', 'Prix vente',
        'Qté stock', 'Seuil', 'Emplacement', 'Auteur', 'Date',
    ]
    rows = [
        [
            i,
            cell(p.numero_piece),
            cell(p.designation),
            cell(getattr(p.sous_categorie, 'nom', None)),
            _fmt_money(p.prix_achat),
            _fmt_money(p.prix_unitaire),
            getattr(p, 'quantite_disponible', 0),
            p.seuil,
            cell(p.emplacement),
            cell(getattr(p.utilisateur, 'username', None)),
            p.date_creation.strftime('%d-%m-%Y') if p.date_creation else '',
        ]
        for i, p in enumerate(pieces, start=1)
    ]
    safe = str(categorie.categorie).replace(' ', '_')[:30]
    return excel_response(
        f"pieces_{safe}.xlsx", headers, rows, sheet_title='Pièces',
    )


@login_required(login_url='connexion')
@require_GET
def export_pieces_categorie_pdf(request, pk):
    pieces, categorie = _qs_pieces_categorie(request, pk)
    headers = ['N°#', 'N°Pièce', 'Désignation', 'Sous-cat.', 'Prix vente', 'Qté', 'Seuil', 'Date']
    rows = [
        [
            i,
            cell(p.numero_piece),
            cell(p.designation),
            cell(getattr(p.sous_categorie, 'nom', None)),
            _fmt_money(p.prix_unitaire),
            getattr(p, 'quantite_disponible', 0),
            p.seuil,
            p.date_creation.strftime('%d-%m-%Y') if p.date_creation else '',
        ]
        for i, p in enumerate(pieces, start=1)
    ]
    safe = str(categorie.categorie).replace(' ', '_')[:30]
    return pdf_response(
        request,
        title=f'Pièces — {categorie.categorie}',
        subtitle='',
        headers=headers,
        rows=rows,
        filename=f"pieces_{safe}.pdf",
    )


# ── Livraisons magasin ────────────────────────────────────────────────────

def _qs_livraisons(request):
    from stock.views import _qs_paniers_livraison_magasin

    filt = periode_filter_context(request, request.user, reset_url_name='livraisons')
    localite = filt['localite_active']
    qs = (
        _qs_paniers_livraison_magasin(localite)
        .filter(
            panier_livre=False,
            date_creation__range=[filt['date_debut'], filt['date_fin']],
        )
        .select_related('utilisateur')
        .prefetch_related('commands')
        .order_by('-date_creation')
    )
    return qs, filt


@login_required(login_url='connexion')
@require_GET
def export_livraisons_excel(request):
    paniers, filt = _qs_livraisons(request)
    headers = ['N°#', 'N°Cmd', 'Ticket', 'Total', 'Date commande', 'Livreur', 'Statut']
    rows = []
    for i, p in enumerate(paniers, start=1):
        cmd = p.commands.first() if hasattr(p, 'commands') else None
        rows.append([
            i,
            cell(getattr(cmd, 'numero_commande', None)),
            cell(p.ticket),
            _fmt_money(getattr(p, 'total', None)),
            p.date_creation.strftime('%d-%m-%Y') if p.date_creation else '',
            cell(getattr(p.utilisateur, 'username', None)),
            'En attente' if not p.panier_livre else 'Livré',
        ])
    return excel_response(
        f"livraisons_{filt['date_debut']}_{filt['date_fin']}.xlsx",
        headers, rows, sheet_title='Livraisons',
    )


@login_required(login_url='connexion')
@require_GET
def export_livraisons_pdf(request):
    paniers, filt = _qs_livraisons(request)
    headers = ['N°#', 'N°Cmd', 'Ticket', 'Total', 'Date', 'Statut']
    rows = []
    for i, p in enumerate(paniers, start=1):
        cmd = p.commands.first() if hasattr(p, 'commands') else None
        rows.append([
            i,
            cell(getattr(cmd, 'numero_commande', None)),
            cell(p.ticket),
            _fmt_money(getattr(p, 'total', None)),
            p.date_creation.strftime('%d-%m-%Y') if p.date_creation else '',
            'En attente' if not p.panier_livre else 'Livré',
        ])
    return pdf_response(
        request,
        title='Livraisons magasin',
        subtitle=filt.get('filtre_resume', ''),
        headers=headers,
        rows=rows,
        filename=f"livraisons_{filt['date_debut']}_{filt['date_fin']}.pdf",
    )


# ── Commandes en ligne ────────────────────────────────────────────────────

def _qs_cmd_line(request):
    from ecommerce.views import _require_staff_cmd, _staff_local
    from Userauths.models import CustomUser
    from stock.period_filters import build_filtre_resume

    if not _require_staff_cmd(request.user):
        return None, None
    user = request.user
    filt = periode_filter_context(
        request, user, reset_url_name='cmd_line', default='mois',
    )
    can_choose = bool(user.is_superuser or getattr(user, 'role', None) == 'admin')
    if can_choose:
        localite = filt.get('localite_active')
    else:
        localite = _staff_local(user)
        filt['localite_active'] = localite
    filt['filtre_resume'] = build_filtre_resume(
        filt['date_debut'], filt['date_fin'], filt['periode_active'], localite,
    )
    qs = Commande.objects.filter(
        commande_en_ligne=True,
        date_creation__range=[filt['date_debut'], filt['date_fin']],
    ).select_related(
        'panier', 'panier__utilisateur', 'panier__local_entrepot', 'livreur',
    ).order_by('-date')
    if localite:
        qs = qs.filter(panier__local_entrepot=localite)
    return qs, filt


def _cmd_line_rows(commandes):
    headers = [
        'N°#', 'N° commande', 'Client', 'Localité', 'Total',
        'Mode réception', 'Statut', 'Payé', 'Livreur',
    ]
    rows = []
    for i, cmd in enumerate(commandes, start=1):
        client = (
            getattr(getattr(cmd.panier, 'utilisateur', None), 'username', None)
            or getattr(cmd.panier, 'nom_client', None)
        )
        rows.append([
            i,
            cell(cmd.numero_commande),
            cell(client),
            cell(getattr(getattr(cmd.panier, 'local_entrepot', None), 'nom', None)),
            _fmt_money(cmd.total),
            cell(cmd.panier.get_mode_reception_display() if cmd.panier else None),
            cell(cmd.get_statut_commande_display()),
            _fmt_bool_paye(cmd.paye),
            cell(getattr(cmd.livreur, 'username', None)),
        ])
    return headers, rows


@login_required(login_url='connexion')
@require_GET
def export_cmd_line_excel(request):
    qs, filt = _qs_cmd_line(request)
    if qs is None:
        return redirect('tbord')
    headers, rows = _cmd_line_rows(qs)
    return excel_response(
        f"cmd_ligne_{filt['date_debut']}_{filt['date_fin']}.xlsx",
        headers, rows, sheet_title='Cmd ligne',
    )


@login_required(login_url='connexion')
@require_GET
def export_cmd_line_pdf(request):
    qs, filt = _qs_cmd_line(request)
    if qs is None:
        return redirect('tbord')
    headers, rows = _cmd_line_rows(qs)
    return pdf_response(
        request,
        title='Commandes en ligne',
        subtitle=filt.get('filtre_resume', ''),
        headers=headers,
        rows=rows,
        filename=f"cmd_ligne_{filt['date_debut']}_{filt['date_fin']}.pdf",
    )


# ── Livraisons commandes en ligne ─────────────────────────────────────────

def _qs_livraison_cmd_online(request):
    from ecommerce.views import _require_livreur, _require_magasin, _staff_local
    from ecommerce.services import filter_commandes_livraison_online_for_user
    from stock.period_filters import build_filtre_resume

    if not _require_magasin(request.user):
        return None, None
    user = request.user
    filt = periode_filter_context(
        request, user, reset_url_name='livraison_cmd_online', default='semaine',
    )
    is_livreur = _require_livreur(user)
    can_choose = (
        not is_livreur
        and bool(user.is_superuser or getattr(user, 'role', None) == 'admin')
    )
    if can_choose:
        localite = filt.get('localite_active')
    elif is_livreur:
        localite = None
    else:
        localite = _staff_local(user)
        filt['localite_active'] = localite
    filt['filtre_resume'] = build_filtre_resume(
        filt['date_debut'], filt['date_fin'], filt['periode_active'], localite,
    )
    qs = Commande.objects.filter(
        commande_en_ligne=True,
        date_creation__range=[filt['date_debut'], filt['date_fin']],
        livreur__isnull=False,
    ).select_related(
        'panier', 'panier__utilisateur', 'panier__local_entrepot', 'livreur',
    ).order_by('-date')
    qs = filter_commandes_livraison_online_for_user(
        user, qs, localite=localite if can_choose else None,
    )
    return qs, filt


@login_required(login_url='connexion')
@require_GET
def export_livraison_cmd_online_excel(request):
    qs, filt = _qs_livraison_cmd_online(request)
    if qs is None:
        return redirect('tbord')
    headers = [
        'N°#', 'N° commande', 'Client', 'Localité', 'Mode', 'Total',
        'Statut', 'Paiement', 'Livreur',
    ]
    rows = []
    for i, cmd in enumerate(qs, start=1):
        client = (
            getattr(getattr(cmd.panier, 'utilisateur', None), 'username', None)
            or getattr(cmd.panier, 'nom_client', None)
        )
        rows.append([
            i,
            cell(cmd.numero_commande),
            cell(client),
            cell(getattr(getattr(cmd.panier, 'local_entrepot', None), 'nom', None)),
            cell(cmd.panier.get_mode_reception_display() if cmd.panier else None),
            _fmt_money(cmd.total),
            cell(cmd.get_statut_commande_display()),
            _fmt_bool_paye(cmd.paye),
            cell(getattr(cmd.livreur, 'username', None)),
        ])
    return excel_response(
        f"livraison_online_{filt['date_debut']}_{filt['date_fin']}.xlsx",
        headers, rows, sheet_title='Livr. online',
    )


@login_required(login_url='connexion')
@require_GET
def export_livraison_cmd_online_pdf(request):
    qs, filt = _qs_livraison_cmd_online(request)
    if qs is None:
        return redirect('tbord')
    headers = ['N°#', 'N° commande', 'Client', 'Total', 'Statut', 'Paiement', 'Livreur']
    rows = []
    for i, cmd in enumerate(qs, start=1):
        client = (
            getattr(getattr(cmd.panier, 'utilisateur', None), 'username', None)
            or getattr(cmd.panier, 'nom_client', None)
        )
        rows.append([
            i,
            cell(cmd.numero_commande),
            cell(client),
            _fmt_money(cmd.total),
            cell(cmd.get_statut_commande_display()),
            _fmt_bool_paye(cmd.paye),
            cell(getattr(cmd.livreur, 'username', None)),
        ])
    return pdf_response(
        request,
        title='Livraisons en ligne',
        subtitle=filt.get('filtre_resume', ''),
        headers=headers,
        rows=rows,
        filename=f"livraison_online_{filt['date_debut']}_{filt['date_fin']}.pdf",
    )
