"""Données partagées MES VENTES (liste + export Excel)."""
from datetime import date

from django.db.models import Sum

from .models import PanierItem, Piece
from .period_filters import build_table_period_columns, periode_filter_context
from .stock_local_service import get_prix_unitaire


def build_mes_ventes_data(request):
    """
    Calcule le tableau MES VENTES selon le filtre GET courant.
    Défaut période = mois en cours si aucun filtre.
    """
    filt = periode_filter_context(
        request, request.user, reset_url_name='mesventes', default='mois',
    )
    date_debut = filt['date_debut']
    date_fin = filt['date_fin']
    periode_active = filt['periode_active']
    localite = filt['localite_active']
    today = date.today()

    period_columns, table_mode = build_table_period_columns(
        date_debut, date_fin, periode_active,
    )
    n_cols = len(period_columns)

    pieces = Piece.objects.select_related('categorie', 'sous_categorie').all()
    if localite:
        pieces = pieces.filter(stocks__local_entrepot=localite).distinct()

    def item_qs():
        qs = PanierItem.objects.filter(panier__panier_paye=True, panier__valide=True)
        if localite:
            qs = qs.filter(panier__local_entrepot=localite)
        return qs

    vente_details = []
    sum_vent_jour = sum_cout_jour = sum_vent_mois = sum_cout_mois = sum_vent_an = sum_cout_an = 0
    daily_totals = [0] * n_cols
    daily_costs = [0.0] * n_cols
    year_ref = date_fin.year

    for p in pieces:
        # Prix local si défini, sinon catalogue
        pu = float(get_prix_unitaire(p, localite))
        period_actions = []
        period_costs_piece = []
        for i, col in enumerate(period_columns):
            qset = item_qs().filter(piece=p, date_creation__range=[col['start'], col['end']])
            qty = qset.aggregate(somme=Sum('quantite'))['somme'] or 0
            cost = qty * pu
            period_actions.append(qty)
            period_costs_piece.append(cost)
            daily_totals[i] += qty
            daily_costs[i] += cost

        vent_jour = item_qs().filter(piece=p, date_creation=today).aggregate(
            total=Sum('quantite'))['total'] or 0
        cout_jour = vent_jour * pu
        vent_mois = item_qs().filter(
            piece=p, date_creation__range=[date_debut, date_fin]
        ).aggregate(total=Sum('quantite'))['total'] or 0
        cout_mois = vent_mois * pu
        vent_an = item_qs().filter(
            piece=p, date_creation__year=year_ref
        ).aggregate(total=Sum('quantite'))['total'] or 0
        cout_an = vent_an * pu

        sum_vent_jour += vent_jour
        sum_cout_jour += cout_jour
        sum_vent_mois += vent_mois
        sum_cout_mois += cout_mois
        sum_vent_an += vent_an
        sum_cout_an += cout_an

        vente_details.append({
            'piece': p,
            'designation': p.designation,
            'numero_piece': p.numero_piece,
            'categorie': p.categorie.categorie,
            'prix_unitaire': pu,
            'daily_actions': period_actions,
            'daily_costs': period_costs_piece,
            'vent_jour': vent_jour,
            'cout_jour': cout_jour,
            'vent_mois': vent_mois,
            'cout_mois': cout_mois,
            'vent_an': vent_an,
            'cout_an': cout_an,
        })

    return {
        'filt': filt,
        'date_debut': date_debut,
        'date_fin': date_fin,
        'periode_active': periode_active,
        'localite': localite,
        'today': today,
        'period_columns': period_columns,
        'period_table_mode': table_mode,
        'n_cols': n_cols,
        'vente_details': vente_details,
        'daily_totals': daily_totals,
        'daily_costs': daily_costs,
        'year_ref': year_ref,
        'total_cols': 2 + n_cols + 6,
        'sum_vent_jour': sum_vent_jour,
        'sum_cout_jour': sum_cout_jour,
        'sum_vent_mois': sum_vent_mois,
        'sum_cout_mois': sum_cout_mois,
        'sum_vent_an': sum_vent_an,
        'sum_cout_an': sum_cout_an,
    }
