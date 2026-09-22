"""
Génération PDF « Bon de commande » pour une demande de transfert (ReportLab).
"""
from __future__ import annotations

import io
from decimal import Decimal

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm
from reportlab.pdfgen import canvas

COLOR_HEADER = colors.HexColor('#ff8b00')
COLOR_TITLE = colors.HexColor('#c2410c')
COLOR_TABLE_HEAD = colors.HexColor('#fff7ed')
COLOR_BORDER = colors.HexColor('#fed7aa')
COLOR_BORDER_LIGHT = colors.HexColor('#e7e5e4')
COLOR_LABEL = colors.HexColor('#6b6b6b')
COLOR_NOTES_BG = colors.HexColor('#fafaf8')


def titre_bon_commande(demande) -> str:
    """Titre affiché dans l'onglet / lecteur PDF (métadonnée Title)."""
    return f"Bon de commande {demande.numero_demande}"


def _draw_field(c, x, y, label, value, width, line_y_offset=14):
    """Label + valeur + ligne de soulignement."""
    c.setFont('Helvetica', 7)
    c.setFillColor(COLOR_LABEL)
    c.drawString(x, y, label.upper())
    c.setFillColor(colors.black)
    c.setFont('Helvetica', 9)
    text = str(value) if value not in (None, '') else '—'
    c.drawString(x, y - 11, text[:55])
    ly = y - line_y_offset
    c.setStrokeColor(colors.black)
    c.setLineWidth(0.5)
    c.line(x, ly, x + width, ly)
    return ly - 14


def generate_demande_transfert_pdf(demande, lignes, *, total_quantite, total_valeur):
    """
    Retourne les octets du PDF A4 (disposition identique à l'aperçu HTML).
    """
    buffer = io.BytesIO()
    page_w, page_h = A4
    margin = 14 * mm
    content_w = page_w - 2 * margin
    c = canvas.Canvas(buffer, pagesize=A4)
    doc_title = titre_bon_commande(demande)
    c.setTitle(doc_title)
    c.setSubject(doc_title)
    c.setAuthor('P&B AUTO-PIECE')

    y = page_h

    # --- Bandeau vert ---
    header_h = 22 * mm
    y -= header_h
    c.setFillColor(COLOR_HEADER)
    c.rect(0, y, page_w, header_h, fill=1, stroke=0)
    c.setFillColor(colors.white)
    c.setFont('Helvetica-Bold', 14)
    c.drawString(margin, y + 13 * mm, 'P&B AUTO-PIECE')
    c.setFont('Helvetica', 7)
    c.drawString(margin, y + 9 * mm, 'Transfert inter-agences — Pièces détachées')
    c.setFont('Helvetica', 8)
    date_txt = demande.date_demande.strftime('%d/%m/%Y %H:%M') if demande.date_demande else ''
    c.drawRightString(page_w - margin, y + 13 * mm, 'Demande de transfert')
    c.drawRightString(page_w - margin, y + 9 * mm, date_txt)

    y -= 10 * mm
    pad_x = margin

    # --- Titre ---
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

    # --- Métadonnées 2 colonnes ---
    col_w = (content_w - 8 * mm) / 2
    col2_x = pad_x + col_w + 8 * mm
    y_left = y
    y_right = y

    num_cmd = demande.numero_bon_donneur or demande.numero_demande
    y_left = _draw_field(c, pad_x, y_left, 'Numéro de commande', num_cmd, col_w)
    y_left = _draw_field(c, pad_x, y_left, 'N° demande', demande.numero_demande, col_w)
    y_left = _draw_field(c, pad_x, y_left, 'Date', demande.date_demande.strftime('%d/%m/%Y'), col_w)
    y_left = _draw_field(c, pad_x, y_left, 'Agence demandeur', demande.local_demandeur.nom, col_w)

    y_right = _draw_field(c, col2_x, y_right, 'Agence donneuse', demande.local_donneur.nom, col_w)
    y_right = _draw_field(c, col2_x, y_right, 'Statut', demande.get_statut_display(), col_w)
    demandeur_name = demande.demandeur.username if demande.demandeur else '—'
    y_right = _draw_field(c, col2_x, y_right, 'Demandeur (utilisateur)', demandeur_name, col_w)
    if demande.numero_bon_commande:
        y_right = _draw_field(c, col2_x, y_right, 'Bon réception', demande.numero_bon_commande, col_w)

    y = min(y_left, y_right) - 6 * mm

    # --- Tableau pièces ---
    col_widths = [0.18, 0.36, 0.12, 0.17, 0.17]
    headers = ['N° pièce', 'Désignation', 'Quantité', 'Prix unit.', 'Total']
    row_h = 7 * mm
    header_h_row = 8 * mm

    def col_x(i):
        return pad_x + sum(content_w * col_widths[j] for j in range(i))

    # En-tête tableau
    c.setFillColor(COLOR_TABLE_HEAD)
    c.rect(pad_x, y - header_h_row, content_w, header_h_row, fill=1, stroke=0)
    c.setFillColor(COLOR_BORDER)
    c.rect(pad_x, y - header_h_row, content_w, header_h_row, fill=0, stroke=1)
    c.setFillColor(colors.HexColor('#c2410c'))
    c.setFont('Helvetica-Bold', 7)
    for i, h in enumerate(headers):
        x = col_x(i) + 3
        if i >= 2:
            c.drawRightString(col_x(i + 1) - 3, y - 5.5 * mm, h.upper())
        else:
            c.drawString(x, y - 5.5 * mm, h.upper())
    y -= header_h_row

    min_table_rows = 8
    table_rows = list(lignes)
    while len(table_rows) < min_table_rows:
        table_rows.append(None)

    c.setFont('Helvetica', 9)
    c.setFillColor(colors.black)
    for row in table_rows:
        c.setStrokeColor(COLOR_BORDER_LIGHT)
        c.rect(pad_x, y - row_h, content_w, row_h, fill=0, stroke=1)
        if row is not None:
            piece = row.piece
            pu = piece.prix_unitaire
            total_ligne = pu * row.quantite
            cells = [
                piece.numero_piece,
                piece.designation[:42],
                str(row.quantite),
                f'{int(pu)} F',
                f'{int(total_ligne)} F',
            ]
            for i, cell in enumerate(cells):
                if i >= 2:
                    c.drawRightString(col_x(i + 1) - 4, y - 5 * mm, cell)
                else:
                    c.drawString(col_x(i) + 4, y - 5 * mm, cell)
        y -= row_h

    y -= 8 * mm

    # --- Pied de page ---
    footer_left_w = content_w * 0.58
    footer_right_x = pad_x + footer_left_w + 6 * mm
    footer_right_w = content_w - footer_left_w - 6 * mm
    notes_h = 32 * mm

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
    if demande.motif:
        c.setFont('Helvetica-Bold', 8)
        c.drawString(pad_x + 5, ty, 'Motif :')
        c.setFont('Helvetica', 8)
        c.drawString(pad_x + 32, ty, str(demande.motif)[:60])
        ty -= 4 * mm
    c.drawString(
        pad_x + 5, ty,
        f'Transfert de {demande.local_donneur.nom} vers {demande.local_demandeur.nom}.',
    )
    ty -= 4 * mm
    if demande.date_validation:
        txt = f'Validée le {demande.date_validation.strftime("%d/%m/%Y %H:%M")}'
        if demande.validateur_donneur:
            txt += f' par {demande.validateur_donneur.username}'
        txt += '.'
        c.drawString(pad_x + 5, ty, txt[:75])
        ty -= 4 * mm
    if demande.date_reception:
        txt = f'Reçue le {demande.date_reception.strftime("%d/%m/%Y %H:%M")}'
        if demande.receveur:
            txt += f' par {demande.receveur.username}'
        txt += '.'
        c.drawString(pad_x + 5, ty, txt[:75])

    # Résumé à droite
    sy = y
    c.setFont('Helvetica', 8)
    c.setFillColor(colors.black)
    summaries = [
        ('Nombre de lignes', str(len(lignes))),
        ('Quantité totale', str(total_quantite)),
        ('Valeur indicative', f'{int(total_valeur)} Fcfa'),
    ]
    for label, val in summaries:
        c.drawString(footer_right_x, sy, label)
        c.drawRightString(footer_right_x + footer_right_w, sy, val)
        sy -= 5 * mm

    sy -= 6 * mm
    c.drawString(footer_right_x, sy, 'Date : _______________')
    sy -= 5 * mm
    c.drawString(footer_right_x, sy, 'À : _______________')
    sy -= 5 * mm
    c.drawString(footer_right_x, sy, 'Signature :')
    c.line(footer_right_x, sy - 8 * mm, footer_right_x + footer_right_w * 0.75, sy - 8 * mm)

    c.save()
    buffer.seek(0)
    return buffer.getvalue()
