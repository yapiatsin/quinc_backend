"""
Calcul de la TVA optionnelle en caisse (case à cocher).
La TVA s'ajoute au total marchandises (après remise), en plus du timbre fiscal.
"""
from __future__ import annotations

from decimal import Decimal, ROUND_HALF_UP

from .models import ParametreTVA


def get_parametre_tva() -> ParametreTVA | None:
    """Retourne le paramètre TVA actif (au plus un)."""
    return ParametreTVA.objects.filter(active=True).order_by('-date_maj', '-pk').first()


def tva_est_active() -> bool:
    return get_parametre_tva() is not None


def get_taux_tva() -> Decimal:
    parametre = get_parametre_tva()
    if parametre:
        return Decimal(str(parametre.taux or 0))
    return Decimal('0')


def calculer_tva(montant_total, appliquer_tva: bool = False) -> Decimal:
    """
    Retourne le montant de TVA à ajouter.
    montant_total = total marchandises après remise (Commande.total).
    """
    if not appliquer_tva or not tva_est_active():
        return Decimal('0.00')
    taux = get_taux_tva()
    if taux <= 0:
        return Decimal('0.00')
    montant = Decimal(str(montant_total or 0))
    return (montant * taux / Decimal('100')).quantize(
        Decimal('0.01'), rounding=ROUND_HALF_UP
    )


def total_a_encaisser(montant_total, moyen_paiement, appliquer_tva: bool = False) -> Decimal:
    """Montant réellement dû au guichet (marchandises + TVA + timbre)."""
    from .timbre_service import calculer_timbre

    montant = Decimal(str(montant_total or 0))
    tva = calculer_tva(montant, appliquer_tva)
    timbre, _ = calculer_timbre(montant, moyen_paiement)
    return montant + tva + timbre
