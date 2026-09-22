"""PDF reçu de caisse — même mise en page que l'impression thermique (printer_service.print_receipt)."""
from __future__ import annotations

import os

from django.conf import settings
from reportlab.lib.units import mm
from reportlab.pdfgen import canvas

from stock.receipt_layout import build_receipt_body_lines

RECEIPT_WIDTH = 80 * mm
LINE_HEIGHT = 4.2 * mm
MARGIN_H = 5 * mm
MARGIN_V = 6 * mm
FONT_NAME = "Courier"
FONT_SIZE = 8.5
FONT_SIZE_TITLE = 11
FONT_SIZE_SUBTITLE = 9.5


def _page_height(num_lines: int) -> float:
    return max(120 * mm, MARGIN_V * 2 + num_lines * LINE_HEIGHT + 8 * mm)


def render_receipt_pdf_to_path(commande, panier_items, output_path: str) -> None:
    body_lines = build_receipt_body_lines(commande, panier_items)
    page_h = _page_height(len(body_lines))
    page_w = RECEIPT_WIDTH
    c = canvas.Canvas(output_path, pagesize=(page_w, page_h))

    y = page_h - MARGIN_V
    center_x = page_w / 2

    for idx, (text, align) in enumerate(body_lines):
        is_title = idx == 0
        is_subtitle = text == "REÇU DE CAISSE"
        if is_title:
            c.setFont(f"{FONT_NAME}-Bold", FONT_SIZE_TITLE)
        elif is_subtitle:
            c.setFont(f"{FONT_NAME}-Bold", FONT_SIZE_SUBTITLE)
        elif text.startswith("-") or text.startswith("*"):
            c.setFont(FONT_NAME, FONT_SIZE)
        elif text.startswith("Désignation"):
            c.setFont(f"{FONT_NAME}-Bold", FONT_SIZE)
        elif text in ("Total Brut", "Total Net", "A PAYER") or text.startswith("Total "):
            c.setFont(f"{FONT_NAME}-Bold", FONT_SIZE)
        else:
            c.setFont(FONT_NAME, FONT_SIZE)

        if align == "center":
            c.drawCentredString(center_x, y, text)
        else:
            c.drawString(MARGIN_H, y, text)

        y -= LINE_HEIGHT

    c.save()


def save_receipt_pdf_file(commande, panier_items) -> str:
    """
    Génère le PDF dans media/tickets/ et retourne le chemin relatif (ex. tickets/recu_XXX.pdf).
    """
    output_dir = os.path.join(settings.MEDIA_ROOT, "tickets")
    os.makedirs(output_dir, exist_ok=True)
    safe_num = commande.numero_commande.replace("/", "_").replace(" ", "_")
    filename = f"recu_{safe_num}.pdf"
    output_path = os.path.join(output_dir, filename)
    render_receipt_pdf_to_path(commande, panier_items, output_path)
    return f"tickets/{filename}"
