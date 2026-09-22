"""Mise en page partagée : reçu thermique et PDF (après paiement caisse)."""
from __future__ import annotations

import textwrap
from decimal import Decimal

from stock.stock_local_service import get_prix_unitaire

# Largeur utile identique à EscPosWriter(margin=6, line_width=50) → inner=38
RECEIPT_INNER = 38
DESIGNATION_COL = 20

RECEIPT_FOOTER_LINES = (
    "Merci pour votre achat!",
    "Chez nous la Qualité ou rien !",
    "Contactez-nous au +225 2721737896",
    "Retrouvez-nous sur ",
    "*** P&B Auto-Pieces.@facebook.com ***",
)


def caissier_label(commande) -> str:
    utilisateur = commande.utilisateur
    return utilisateur.username if utilisateur else "—"


def commande_date_label(commande) -> str:
    if commande.date:
        return commande.date.strftime("%d/%m/%Y à %H:%M")
    return commande.date_creation.strftime("%d/%m/%Y")


def spaced_line(left: str, right: str, width: int = RECEIPT_INNER) -> str:
    space = max(1, width - len(left) - len(right))
    return f"{left}{' ' * space}{right}"


def total_line(label: str, value, width: int = RECEIPT_INNER) -> str:
    return spaced_line(label, str(int(value)), width)


def remise_line(commande, width: int = RECEIPT_INNER) -> str | None:
    if not commande.remise or commande.remise <= 0:
        return None
    tb = Decimal(str(commande.total_sans_remise or 0))
    r = Decimal(str(commande.remise or 0))
    pct = (r / tb * Decimal("100")).quantize(Decimal("0.01")) if tb > 0 else Decimal("0")
    remise_val = f"-{pct:.2f} %"
    return spaced_line("Remise", remise_val, width)


def panier_localite(commande):
    if commande.panier_id:
        return getattr(commande.panier, "local_entrepot", None)
    return None


def build_receipt_item_rows(panier_items, commande):
    """
    Lignes articles : ('row', designation, qte, pu, tot) ou ('wrap', texte_suite).
    """
    loc = panier_localite(commande)
    rows = []
    for item in panier_items:
        designation = item.piece.designation
        quantite = item.quantite
        prix = get_prix_unitaire(item.piece, loc, item.new_price)
        total_ligne = prix * quantite
        wrapped = textwrap.wrap(designation, width=DESIGNATION_COL) or [""]
        rows.append(("row", wrapped[0], quantite, int(prix), int(total_ligne)))
        for extra in wrapped[1:]:
            rows.append(("wrap", extra))
    return rows


def build_receipt_body_lines(commande, panier_items):
    """Liste de lignes texte centrées ou alignées à gauche (comme le ticket thermique)."""
    lines: list[tuple[str, str]] = []  # (text, align) align in left|center

    def add(text: str, align: str = "left"):
        lines.append((text, align))

    add("P&B Auto-Pieces", "center")
    add("*" * RECEIPT_INNER, "center")
    add("REÇU DE CAISSE", "center")
    add("*" * RECEIPT_INNER, "center")

    commande_label = f"{commande.numero_commande}"
    date_label = commande_date_label(commande)
    add(spaced_line(commande_label, date_label))
    add(f"Caissier : {caissier_label(commande)}")
    add("-" * RECEIPT_INNER, "center")

    add(f"{'Désignation':<20}{'Qte':>4}{'PU':>7}{'Tot':>7}")

    for kind, *rest in build_receipt_item_rows(panier_items, commande):
        if kind == "row":
            desig, qte, pu, tot = rest
            add(f"{desig:<20}{qte:>4}{pu:>7}{tot:>7}")
        else:
            add(rest[0])

    add("-" * RECEIPT_INNER, "center")

    add(total_line("Total Brut", commande.total_sans_remise))
    rl = remise_line(commande)
    if rl:
        add(rl)
    add(total_line("Total Net", commande.total))
    tva = Decimal(str(commande.montant_tva or 0))
    if tva > 0:
        add(total_line("TVA", tva))
    timbre = Decimal(str(commande.montant_timbre or 0))
    if timbre > 0:
        add(total_line("Timbre fiscal", timbre))
    a_payer = commande.total_a_encaisser
    if tva > 0 or timbre > 0:
        add(total_line("A PAYER", a_payer))
    add(total_line("Payé", commande.montant_paye))
    add(total_line("Rendu", commande.montant_reste))

    moyen = getattr(commande, "libelle_paiement", None) or "—"
    add(spaced_line("Payer par", moyen))

    add("-" * RECEIPT_INNER, "center")
    for footer in RECEIPT_FOOTER_LINES:
        add(footer, "center")

    return lines
