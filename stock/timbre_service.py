"""
Calcul du timbre fiscal (paiement en espèces).
Le timbre n'est pas inclus dans le chiffre d'affaires (Commande.total).
"""
from __future__ import annotations

import unicodedata
from decimal import Decimal

from .models import BaremeTimbre, MoyenPaiement

CODES_ESPECE = {'espece', 'especes', 'cash', 'liquide', 'espece_cfa'}


def _normaliser_texte(value: str) -> str:
    if not value:
        return ''
    nfd = unicodedata.normalize('NFD', value.strip().lower())
    return ''.join(c for c in nfd if unicodedata.category(c) != 'Mn')


def moyen_paiement_est_espece(moyen_paiement: MoyenPaiement | None) -> bool:
    if moyen_paiement is None:
        return False
    code = _normaliser_texte(moyen_paiement.code or '')
    if code in CODES_ESPECE:
        return True
    nom = _normaliser_texte(moyen_paiement.nom or '')
    return nom in ('espece', 'especes', 'cash') or 'espece' in nom


def calculer_timbre(montant_total, moyen_paiement):
    """
    Retourne (montant_timbre, bareme_applique).
    montant_total = total marchandises après remise (Commande.total).
    """
    if not moyen_paiement_est_espece(moyen_paiement):
        return Decimal('0'), None

    montant = Decimal(str(montant_total or 0))
    if montant < Decimal('5001'):
        return Decimal('0'), None

    for bareme in BaremeTimbre.objects.filter(actif=True).order_by('montant_min'):
        if bareme.contient(montant):
            return bareme.montant_timbre, bareme
    return Decimal('0'), None


def total_a_encaisser(montant_total, moyen_paiement) -> Decimal:
    """Montant réellement dû au guichet (marchandises + timbre)."""
    montant = Decimal(str(montant_total or 0))
    timbre, _ = calculer_timbre(montant, moyen_paiement)
    return montant + timbre
