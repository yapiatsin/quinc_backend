"""Statistiques dashboard client (web + API)."""
from calendar import month_name
from datetime import date
from decimal import Decimal

from django.db.models import Sum
from django.db.models.functions import ExtractMonth

from ecom.models import FavoriPiece, VueRecentePiece
from stock.models import Commande

MOIS_LABELS = {
    1: 'Janvier',
    2: 'Février',
    3: 'Mars',
    4: 'Avril',
    5: 'Mai',
    6: 'Juin',
    7: 'Juillet',
    8: 'Août',
    9: 'Septembre',
    10: 'Octobre',
    11: 'Novembre',
    12: 'Décembre',
}

MOIS_COURTS = {
    1: 'Jan',
    2: 'Fév',
    3: 'Mar',
    4: 'Avr',
    5: 'Mai',
    6: 'Juin',
    7: 'Juil',
    8: 'Août',
    9: 'Sep',
    10: 'Oct',
    11: 'Nov',
    12: 'Déc',
}


def _to_float(value):
    if value is None:
        return 0.0
    if isinstance(value, Decimal):
        return float(value)
    return float(value)


def parse_dashboard_period(mois_raw=None, annee_raw=None):
    today = date.today()
    try:
        annee = int(annee_raw if annee_raw not in (None, '') else today.year)
    except (TypeError, ValueError):
        annee = today.year
    try:
        mois = int(mois_raw if mois_raw not in (None, '') else today.month)
    except (TypeError, ValueError):
        mois = today.month
    if mois < 1 or mois > 12:
        mois = today.month
    annees_disponibles = [today.year - i for i in range(10)]
    if annee not in annees_disponibles:
        annee = today.year
    return mois, annee, annees_disponibles


def client_orders_qs(user):
    return Commande.objects.filter(
        commande_en_ligne=True,
        panier__utilisateur=user,
    )


def build_client_dashboard(user, mois, annee):
    qs = client_orders_qs(user)
    mois_qs = qs.filter(date__year=annee, date__month=mois)
    payees_mois = mois_qs.filter(paye=True)
    annee_qs = qs.filter(date__year=annee)
    total_achats = payees_mois.aggregate(s=Sum('total'))['s'] or Decimal('0')

    montants = {i: Decimal('0') for i in range(1, 13)}
    for row in (
        annee_qs.filter(paye=True)
        .annotate(m=ExtractMonth('date'))
        .values('m')
        .annotate(s=Sum('total'))
    ):
        m = row.get('m')
        if m:
            montants[m] = row.get('s') or Decimal('0')

    max_montant = max(montants.values()) if any(montants.values()) else Decimal('1')
    statut_labels = dict(Commande._meta.get_field('statut_commande').choices)
    recentes = list(qs.order_by('-date')[:5])

    montant_par_mois = [
        {
            'mois': i,
            'label': MOIS_COURTS[i],
            'montant': _to_float(montants[i]),
        }
        for i in range(1, 13)
    ]

    dernieres = []
    for cmd in recentes:
        dt = cmd.date
        dernieres.append({
            'id': cmd.pk,
            'numero_commande': cmd.numero_commande,
            'total': _to_float(cmd.total),
            'statut_commande': cmd.statut_commande,
            'statut_label': statut_labels.get(cmd.statut_commande, cmd.statut_commande),
            'date': dt.isoformat() if dt else None,
        })

    return {
        'total_achats_periode': _to_float(total_achats),
        'nb_commandes_periode': payees_mois.count(),
        'mois': mois,
        'mois_label': MOIS_LABELS.get(mois, month_name[mois] if 1 <= mois <= 12 else ''),
        'annee': annee,
        'annees_disponibles': [date.today().year - i for i in range(10)],
        'commandes_annuelles': annee_qs.count(),
        'commandes_annulees_annuelles': annee_qs.filter(statut_commande='annuler').count(),
        'favoris': FavoriPiece.objects.filter(utilisateur=user).count(),
        'vues_recentes': VueRecentePiece.objects.filter(utilisateur=user).count(),
        'montant_par_mois': montant_par_mois,
        'max_montant_mois': _to_float(max_montant),
        'dernieres_commandes': dernieres,
        'commandes_objets': recentes,
    }
