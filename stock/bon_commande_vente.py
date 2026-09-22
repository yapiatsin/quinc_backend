"""
Bon de commande caisse (après paiement) — génération numéro, PDF et contexte d'affichage.
"""
from __future__ import annotations

import io
import os
from decimal import Decimal

from django.conf import settings
from django.utils import timezone
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm
from reportlab.pdfgen import canvas

from .stock_local_service import get_prix_unitaire
from .demande_transfert_pdf import (
    COLOR_BORDER,
    COLOR_BORDER_LIGHT,
    COLOR_HEADER,
    COLOR_LABEL,
    COLOR_NOTES_BG,
    COLOR_TABLE_HEAD,
    COLOR_TITLE,
    _draw_field,
)

def generer_numero_bon_vente() -> str:
    year = timezone.now().year
    from .models import BonCommandePaiement
    count = BonCommandePaiement.objects.filter(
        numero_bon__startswith=f'BCV-{year}-'
    ).count() + 1
    return f'BCV-{year}-{count:04d}'


def titre_bon_commande_vente(bon) -> str:
    return f"Bon de commande {bon.numero_bon}"


def nom_fichier_bon_commande_vente(bon) -> str:
    from urllib.parse import quote
    numero = (bon.numero_bon or 'bon').replace('/', '-').replace('\\', '-')
    nom_ascii = f"Bon_de_commande_{numero}.pdf"
    nom_utf8 = quote(f"Bon de commande {bon.numero_bon}.pdf")
    return nom_ascii, nom_utf8


def _lignes_depuis_panier(panier_items, panier=None):
    lignes = []
    for item in panier_items:
        loc = None
        if panier is not None:
            loc = panier.local_entrepot
        elif getattr(item, 'panier', None) is not None:
            loc = item.panier.local_entrepot
        pu = get_prix_unitaire(item.piece, loc, item.new_price)
        item.prix_unitaire_affiche = pu
        item.total_ligne = pu * item.quantite
        lignes.append(item)
    return lignes


def context_bon_commande_vente(bon, commande, panier, panier_items):
    lignes = _lignes_depuis_panier(panier_items, panier)
    total_quantite = sum(i.quantite for i in lignes)
    total_valeur = sum(i.total_ligne for i in lignes)
    min_rows = 8
    empty_rows = range(max(0, min_rows - len(lignes)))
    localite = bon.local_entrepot or getattr(panier, 'local_entrepot', None)
    return {
        'bon': bon,
        'commande': commande,
        'panier': panier,
        'lignes': lignes,
        'total_quantite': total_quantite,
        'total_valeur': total_valeur,
        'empty_rows': empty_rows,
        'localite': localite,
        'print_title': titre_bon_commande_vente(bon),
        'pdf_filename': nom_fichier_bon_commande_vente(bon)[0],
    }


def generate_bon_commande_vente_pdf(bon, commande, panier, panier_items):
    lignes = _lignes_depuis_panier(panier_items, panier)
    total_quantite = sum(i.quantite for i in lignes)
    total_valeur = int(sum(i.total_ligne for i in lignes))

    buffer = io.BytesIO()
    page_w, page_h = A4
    margin = 14 * mm
    content_w = page_w - 2 * margin
    c = canvas.Canvas(buffer, pagesize=A4)
    doc_title = titre_bon_commande_vente(bon)
    c.setTitle(doc_title)
    c.setSubject(doc_title)
    c.setAuthor('P&B AUTO-PIECE')

    y = page_h
    header_h = 22 * mm
    y -= header_h
    c.setFillColor(COLOR_HEADER)
    c.rect(0, y, page_w, header_h, fill=1, stroke=0)
    c.setFillColor(colors.white)
    c.setFont('Helvetica-Bold', 14)
    c.drawString(margin, y + 13 * mm, 'P&B AUTO-PIECE')
    c.setFont('Helvetica', 7)
    c.drawString(margin, y + 9 * mm, 'Vente — Pièces détachées')
    c.setFont('Helvetica', 8)
    date_txt = bon.date_emission.strftime('%d/%m/%Y %H:%M')
    c.drawRightString(page_w - margin, y + 13 * mm, 'Bon de commande — Caisse')
    c.drawRightString(page_w - margin, y + 9 * mm, date_txt)

    y -= 10 * mm
    pad_x = margin

    c.setFillColor(COLOR_TITLE)
    c.setFont('Times-Roman', 22)
    title = 'BON DE COMMANDE'
    tw = c.stringWidth(title, 'Times-Roman', 22)
    c.drawString((page_w - tw) / 2, y, title)
    y -= 5 * mm
    c.setStrokeColor(COLOR_TITLE)
    c.setLineWidth(0.6)
    line_w = content_w * 0.85
    c.line((page_w - line_w) / 2, y, (page_w + line_w) / 2, y)
    y -= 10 * mm

    col_w = (content_w - 8 * mm) / 2
    col2_x = pad_x + col_w + 8 * mm
    y_left = y
    y_right = y

    local_nom = str(bon.local_entrepot) if bon.local_entrepot_id else '—'
    date_aff = commande.date.strftime('%d/%m/%Y %H:%M') if commande.date else commande.date_creation.strftime('%d/%m/%Y')

    y_left = _draw_field(c, pad_x, y_left, 'Numéro de commande', bon.numero_bon, col_w)
    y_left = _draw_field(c, pad_x, y_left, 'N° commande', commande.numero_commande, col_w)
    y_left = _draw_field(c, pad_x, y_left, 'Date', date_aff, col_w)
    y_left = _draw_field(c, pad_x, y_left, 'Agence / Localité', local_nom, col_w)

    y_right = _draw_field(c, col2_x, y_right, 'N° ticket', bon.ticket_numero, col_w)
    y_right = _draw_field(c, col2_x, y_right, 'Statut', 'Payée', col_w)
    hote = bon.hote_accueil.username if bon.hote_accueil_id else '—'
    y_right = _draw_field(c, col2_x, y_right, 'Hôte (accueil)', hote, col_w)
    caissier = bon.caissier.username if bon.caissier_id else '—'
    y_right = _draw_field(c, col2_x, y_right, 'Caissier(ère)', caissier, col_w)
    if bon.client_nom:
        y_right = _draw_field(c, col2_x, y_right, 'Client', bon.client_nom, col_w)

    y = min(y_left, y_right) - 6 * mm

    col_widths = [0.18, 0.36, 0.12, 0.17, 0.17]
    headers = ['N° pièce', 'Désignation', 'Quantité', 'Prix unit.', 'Total']
    row_h = 7 * mm
    header_h_row = 8 * mm

    def col_x(i):
        return pad_x + sum(content_w * col_widths[j] for j in range(i))

    c.setFillColor(COLOR_TABLE_HEAD)
    c.rect(pad_x, y - header_h_row, content_w, header_h_row, fill=1, stroke=0)
    c.setFillColor(COLOR_BORDER)
    c.rect(pad_x, y - header_h_row, content_w, header_h_row, fill=0, stroke=1)
    c.setFillColor(colors.HexColor('#c2410c'))
    c.setFont('Helvetica-Bold', 7)
    for i, h in enumerate(headers):
        if i >= 2:
            c.drawRightString(col_x(i + 1) - 3, y - 5.5 * mm, h.upper())
        else:
            c.drawString(col_x(i) + 3, y - 5.5 * mm, h.upper())
    y -= header_h_row

    table_rows = list(lignes)
    while len(table_rows) < 8:
        table_rows.append(None)

    c.setFont('Helvetica', 9)
    for row in table_rows:
        c.setStrokeColor(COLOR_BORDER_LIGHT)
        c.rect(pad_x, y - row_h, content_w, row_h, fill=0, stroke=1)
        if row is not None:
            pu = row.prix_unitaire_affiche
            cells = [
                row.piece.numero_piece,
                row.piece.designation[:42],
                str(row.quantite),
                f'{int(pu)} F',
                f'{int(row.total_ligne)} F',
            ]
            for i, cell in enumerate(cells):
                if i >= 2:
                    c.drawRightString(col_x(i + 1) - 4, y - 5 * mm, cell)
                else:
                    c.drawString(col_x(i) + 4, y - 5 * mm, cell)
        y -= row_h

    y -= 8 * mm
    footer_left_w = content_w * 0.58
    footer_right_x = pad_x + footer_left_w + 6 * mm
    footer_right_w = content_w - footer_left_w - 6 * mm
    notes_h = 36 * mm

    c.setFont('Helvetica', 7)
    c.setFillColor(COLOR_LABEL)
    c.drawString(pad_x, y, 'NOTES / MOTIF')
    y -= 3 * mm
    c.setFillColor(COLOR_NOTES_BG)
    c.setStrokeColor(COLOR_BORDER)
    c.rect(pad_x, y - notes_h, footer_left_w, notes_h, fill=1, stroke=1)
    c.setFillColor(colors.black)
    c.setFont('Helvetica', 8)
    ty = y - 5 * mm
    if bon.moyen_paiement_nom:
        c.drawString(pad_x + 5, ty, f"Paiement : {bon.moyen_paiement_nom}")
        ty -= 4 * mm
    if commande.remise and commande.remise > 0:
        c.drawString(pad_x + 5, ty, f"Remise appliquée : {int(commande.remise)} Fcfa")
        ty -= 4 * mm
    c.drawString(pad_x + 5, ty, "Commande validée par le service accueil, réglée en caisse.")
    ty -= 4 * mm
    c.drawString(pad_x + 5, ty, f"Ticket : {bon.ticket_numero}")

    sy = y
    summaries = [
        ('Nombre de lignes', str(len(lignes))),
        ('Quantité totale', str(total_quantite)),
        ('Total brut', f'{int(commande.total_sans_remise)} Fcfa'),
    ]
    if commande.remise and commande.remise > 0:
        summaries.append(('Remise', f'-{int(commande.remise)} Fcfa'))
    summaries.append(('Total net', f'{int(commande.total)} Fcfa'))
    if commande.montant_tva and commande.montant_tva > 0:
        summaries.append(('TVA', f'{int(commande.montant_tva)} Fcfa'))
    if commande.montant_timbre and commande.montant_timbre > 0:
        summaries.append(('Timbre fiscal', f'{int(commande.montant_timbre)} Fcfa'))
    summaries.extend([
        ('À payer', f'{int(commande.total_a_encaisser)} Fcfa'),
        ('Montant payé', f'{int(commande.montant_paye)} Fcfa'),
        ('Rendu', f'{int(commande.montant_reste)} Fcfa'),
    ])
    for label, val in summaries:
        c.setFont('Helvetica-Bold' if label == 'À payer' else 'Helvetica', 8)
        c.drawString(footer_right_x, sy, label)
        c.drawRightString(footer_right_x + footer_right_w, sy, val)
        sy -= 5 * mm

    sy -= 6 * mm
    c.setFont('Helvetica', 8)
    c.drawString(footer_right_x, sy, 'Date : _______________')
    sy -= 5 * mm
    c.drawString(footer_right_x, sy, 'À : _______________')
    sy -= 5 * mm
    c.drawString(footer_right_x, sy, 'Signature :')
    c.line(footer_right_x, sy - 8 * mm, footer_right_x + footer_right_w * 0.75, sy - 8 * mm)

    c.save()
    buffer.seek(0)
    return buffer.getvalue()


def sauvegarder_pdf_bon(bon, pdf_bytes):
    filename = f"bon_vente_{bon.numero_bon.replace('/', '-')}.pdf"
    output_dir = os.path.join(settings.MEDIA_ROOT, 'bons_vente')
    os.makedirs(output_dir, exist_ok=True)
    output_path = os.path.join(output_dir, filename)
    with open(output_path, 'wb') as f:
        f.write(pdf_bytes)
    return f"bons_vente/{filename}"


def creer_bon_commande_paiement(commande, panier, ticket, caissier):
    """Crée le bon de commande en base + fichier PDF après paiement validé."""
    from .models import BonCommandePaiement

    panier_items = list(panier.panier_items.select_related('piece').all())
    hote = panier.utilisateur
    if not hote and commande.utilisateur_id:
        hote = commande.utilisateur

    bon = BonCommandePaiement.objects.create(
        numero_bon=generer_numero_bon_vente(),
        commande=commande,
        ticket_numero=ticket.numero,
        local_entrepot=panier.local_entrepot,
        caissier=caissier,
        hote_accueil=hote,
        client_nom=panier.nom_client or '',
        moyen_paiement_nom=commande.libelle_paiement if commande.libelle_paiement != '—' else '',
        montant_paye=commande.montant_paye,
        montant_reste=commande.montant_reste,
        total_commande=commande.total,
    )
    pdf_bytes = generate_bon_commande_vente_pdf(bon, commande, panier, panier_items)
    bon.fichier_pdf = sauvegarder_pdf_bon(bon, pdf_bytes)
    bon.save(update_fields=['fichier_pdf'])
    return bon
