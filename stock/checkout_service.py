from decimal import Decimal, InvalidOperation

from .timbre_service import calculer_timbre
from .tva_service import calculer_tva, tva_est_active


def parse_decimal(value, default='0.00'):
    try:
        return Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return Decimal(default)


def compute_checkout_totals(*, total, moyen_paiement=None, appliquer_tva=False):
    montant_total = parse_decimal(total)
    apply_tva = bool(appliquer_tva and tva_est_active())
    montant_tva = calculer_tva(montant_total, apply_tva)
    montant_timbre, bareme = calculer_timbre(montant_total, moyen_paiement)
    total_a_payer = montant_total + montant_tva + montant_timbre
    return {
        'total': montant_total,
        'tva': montant_tva,
        'timbre': montant_timbre,
        'bareme': bareme,
        'total_a_payer': total_a_payer,
        'tva_appliquee': apply_tva,
    }
