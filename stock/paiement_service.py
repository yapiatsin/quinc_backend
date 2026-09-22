"""Orchestration des paiements GeniusPay (e-com + caisse)."""
from __future__ import annotations

import logging
from decimal import Decimal

from django.conf import settings
from django.db import transaction
from django.template.loader import render_to_string
from django.urls import reverse
from django.utils import timezone

from stock.checkout_service import compute_checkout_totals
from stock.geniuspay import (
    GeniusPayError,
    initier_paiement,
    montant_xof,
    normalize_phone_for_api,
    recuperer_paiement,
)
from stock.paiement_labels import extraire_canal_depuis_payload
from stock.models import (
    Commande,
    GeniusPayPaiement,
    PanierItem,
    Ticket,
)
from stock.mqtt_service import publish_paiement_valide
from stock.stock_local_service import decrementer_stock

logger = logging.getLogger(__name__)

STATUTS_OUVERTS = ('pending', 'processing')
STATUTS_ECHEC = ('failed', 'cancelled', 'expired')


def public_base_url(request=None) -> str:
    base = (getattr(settings, 'PUBLIC_BASE_URL', None) or '').rstrip('/')
    if base:
        return base
    if request is not None:
        return request.build_absolute_uri('/').rstrip('/')
    return 'http://127.0.0.1:8000'


def abs_url(name, request=None, query=None, **kwargs) -> str:
    path = reverse(name, kwargs=kwargs or None)
    url = public_base_url(request) + path
    if query:
        from urllib.parse import urlencode
        url = f'{url}?{urlencode(query)}'
    return url


def normalize_ci_phone(raw: str) -> str:
    return normalize_phone_for_api(raw)


def customer_from_commande(commande: Commande) -> dict:
    panier = commande.panier
    user = panier.utilisateur
    name = (
        (panier.nom_client or '').strip()
        or ((user.get_full_name() if user else '') or '')
        or (getattr(user, 'username', '') if user else '')
        or 'Client'
    )
    email = (getattr(user, 'email', '') or '') if user else ''
    phone_raw = (
        (panier.telephone_livraison or '')
        or (getattr(user, 'contact', '') if user else '')
        or ''
    )
    return {
        'name': name[:120],
        'email': email,
        'phone': normalize_ci_phone(phone_raw),
        'country': 'CI',
    }


def montant_correspond(attendu, recu) -> bool:
    try:
        return montant_xof(attendu) == montant_xof(recu)
    except Exception:
        return False


def dernier_paiement_ouvert(commande: Commande) -> GeniusPayPaiement | None:
    return (
        GeniusPayPaiement.objects.filter(
            commande=commande,
            statut__in=STATUTS_OUVERTS,
        )
        .order_by('-date_creation')
        .first()
    )


def initier_paiement_commande(
    commande: Commande,
    *,
    source: str,
    request=None,
    appliquer_tva: bool = False,
    caissier=None,
    amount=None,
) -> GeniusPayPaiement:
    from ecom.services import get_moyen_geniuspay

    moyen = get_moyen_geniuspay()
    if commande.moyen_paiement_id != moyen.pk:
        commande.moyen_paiement = moyen
        commande.save(update_fields=['moyen_paiement'])

    if amount is None:
        if source == GeniusPayPaiement.SOURCE_CAISSE:
            totals = compute_checkout_totals(
                total=commande.total,
                moyen_paiement=moyen,
                appliquer_tva=appliquer_tva,
            )
            amount = totals['total_a_payer']
        else:
            amount = commande.total

    amount_int = montant_xof(amount)
    existing = dernier_paiement_ouvert(commande)
    if (
        existing
        and existing.checkout_url
        and montant_xof(existing.amount) == amount_int
        and bool(existing.appliquer_tva) == bool(appliquer_tva)
    ):
        return existing

    ticket = Ticket.objects.filter(commande=commande).first()
    metadata = {
        'source': source,
        'commande_id': str(commande.pk),
        'panier_id': str(commande.panier_id),
        'numero_commande': commande.numero_commande or '',
    }
    if ticket:
        metadata['ticket_numero'] = ticket.numero

    if source == GeniusPayPaiement.SOURCE_CAISSE:
        success_url = abs_url(
            'caisse_geniuspay_retour',
            request,
            query={'ticket': ticket.numero if ticket else '', 'commande': commande.pk},
        )
        error_url = abs_url(
            'caisse_geniuspay_retour',
            request,
            query={
                'ticket': ticket.numero if ticket else '',
                'commande': commande.pk,
                'status': 'error',
            },
        )
        description = f'Caisse ticket {ticket.numero if ticket else commande.numero_commande}'
    else:
        path = getattr(request, 'path', '') or ''
        if request is not None and '/api/' in path:
            success_url = abs_url(
                'v1_order_payment_return',
                request,
                query={'retour': 'ok'},
                commande_id=str(commande.pk),
            )
            error_url = abs_url(
                'v1_order_payment_return',
                request,
                query={'retour': 'erreur'},
                commande_id=str(commande.pk),
            )
        else:
            success_url = abs_url(
                'ecom_checkout_pay',
                request,
                query={'retour': 'ok'},
                commande_id=str(commande.pk),
            )
            error_url = abs_url(
                'ecom_checkout_pay',
                request,
                query={'retour': 'erreur'},
                commande_id=str(commande.pk),
            )
        description = f'Commande {commande.numero_commande}'

    data = initier_paiement(
        amount_int,
        description=description,
        customer=customer_from_commande(commande),
        metadata=metadata,
        success_url=success_url,
        error_url=error_url,
    )
    reference = data.get('reference')
    if not reference:
        raise GeniusPayError('GeniusPay n’a pas renvoyé de référence.')

    checkout_url = data.get('checkout_url') or data.get('payment_url') or ''
    gp = GeniusPayPaiement.objects.create(
        commande=commande,
        reference=reference,
        source=source,
        statut=data.get('status') or 'pending',
        amount=Decimal(str(data.get('amount', amount_int))),
        checkout_url=checkout_url,
        environment=data.get('environment') or '',
        appliquer_tva=bool(appliquer_tva),
        caissier=caissier,
        metadata=metadata,
        raw_response=data if isinstance(data, dict) else {},
    )
    return gp


def _appliquer_canal_paiement(gp: GeniusPayPaiement, data: dict | None) -> list[str]:
    code, _label = extraire_canal_depuis_payload(data)
    if not code:
        code, _label = extraire_canal_depuis_payload(
            gp.raw_response if isinstance(gp.raw_response, dict) else {}
        )
    if code and gp.payment_method != code:
        gp.payment_method = code
        return ['payment_method']
    return []


def _maj_statut_local(gp: GeniusPayPaiement, data: dict | None, fallback: str | None = None):
    statut = (data or {}).get('status') or fallback
    fields = list(_appliquer_canal_paiement(gp, data))
    if statut:
        gp.statut = statut
        fields.append('statut')
    if data:
        gp.raw_response = data
        fields.append('raw_response')
        url = data.get('checkout_url') or data.get('payment_url')
        if url:
            gp.checkout_url = url
            fields.append('checkout_url')
    if fields:
        gp.save(update_fields=list(dict.fromkeys(fields)))


def appliquer_confirmation_geniuspay(gp: GeniusPayPaiement, data: dict, request=None):
    """Confirme un paiement GeniusPay completed (idempotent)."""
    from ecom.services import confirmer_paiement_en_ligne, get_moyen_geniuspay

    statut = (data or {}).get('status')
    if statut != 'completed':
        _maj_statut_local(gp, data)
        return gp, False

    if not montant_correspond(gp.amount, data.get('amount', gp.amount)):
        logger.error(
            'GeniusPay montant incohérent ref=%s attendu=%s recu=%s',
            gp.reference,
            gp.amount,
            data.get('amount'),
        )
        raise GeniusPayError('Le montant payé ne correspond pas à la commande.')

    moyen = get_moyen_geniuspay()
    with transaction.atomic():
        # of='self' : `caissier` etant nullable, select_related produit une
        # jointure externe et PostgreSQL refuse d'y appliquer FOR UPDATE.
        # On ne verrouille donc que la ligne de paiement elle-meme.
        gp = GeniusPayPaiement.objects.select_for_update(of=('self',)).select_related(
            'commande', 'commande__panier', 'caissier',
        ).get(pk=gp.pk)
        # Idem : `ticket` et `moyen_paiement` sont nullables.
        commande = Commande.objects.select_for_update(of=('self',)).select_related(
            'panier', 'ticket', 'moyen_paiement',
        ).get(pk=gp.commande_id)
        _appliquer_canal_paiement(gp, data)
        if gp.payment_method:
            gp.save(update_fields=['payment_method'])

        if gp.source == GeniusPayPaiement.SOURCE_ECOM:
            confirmer_paiement_en_ligne(
                commande,
                moyen=moyen,
                montant_paye=commande.total,
                silent_if_paid=True,
            )
        else:
            ticket = Ticket.objects.filter(commande=commande).first()
            panier = commande.panier
            items = list(PanierItem.objects.filter(panier=panier).select_related('piece'))
            finaliser_encaissement_caisse(
                request=request,
                commande=commande,
                panier=panier,
                ticket=ticket,
                moyen_paiement=moyen,
                appliquer_tva=gp.appliquer_tva,
                montant_paye=gp.amount,
                panier_items=items,
                caissier=gp.caissier or (request.user if request and getattr(request, 'user', None) and request.user.is_authenticated else None),
            )
        _maj_statut_local(gp, data, fallback='completed')
    return gp, True


def synchroniser_paiement(gp: GeniusPayPaiement, request=None):
    """GET GeniusPay puis confirme si completed."""
    data = recuperer_paiement(gp.reference)
    if not isinstance(data, dict):
        data = {}
    statut = data.get('status') or gp.statut
    if statut == 'completed':
        return appliquer_confirmation_geniuspay(gp, data, request=request)
    _maj_statut_local(gp, data)
    return gp, False


def payload_succes_caisse(request, commande, panier, panier_items, ticket, bon_paiement):
    from stock.bon_commande_vente import context_bon_commande_vente

    if not bon_paiement:
        return {
            'success': True,
            'pending': False,
            # Le navigateur du poste s'en sert pour recuperer le flux ESC/POS
            # et imprimer le recu sur l'imprimante branchee en local.
            'ticket_numero': ticket.numero,
            'warning': 'Paiement enregistré, bon de commande non généré.',
        }
    ctx_bon = context_bon_commande_vente(bon_paiement, commande, panier, panier_items)
    bon_html = (
        render_to_string('mag/partials/_bon_commande_vente_styles.html', request=request)
        + render_to_string(
            'mag/partials/_bon_commande_vente_body.html', ctx_bon, request=request
        )
    )
    try:
        print_url = reverse(
            'imprimer_bon_commande_vente',
            kwargs={'ticket_numero': ticket.numero},
        )
    except Exception:
        print_url = f'/stocks/caisse/bon-commande/{ticket.numero}/imprimer/'
    return {
        'success': True,
        'pending': False,
        'bon_commande_html': bon_html,
        'print_url': print_url + '?print=1',
        'pdf_url': print_url + '?format=pdf',
        'print_title': ctx_bon['print_title'],
        'numero_bon': bon_paiement.numero_bon,
        # Le navigateur du poste s'en sert pour recuperer le flux ESC/POS et
        # imprimer le recu sur l'imprimante branchee en local.
        'ticket_numero': ticket.numero,
    }


@transaction.atomic
def finaliser_encaissement_caisse(
    *,
    request,
    commande,
    panier,
    ticket,
    moyen_paiement,
    appliquer_tva=False,
    montant_paye=None,
    panier_items=None,
    caissier=None,
):
    """Encaissement caisse après paiement (espèces ou GeniusPay confirmé). Idempotent."""
    from stock.bon_commande_vente import creer_bon_commande_paiement

    # of='self' : `moyen_paiement` et `ticket` nullables -> jointure externe,
    # sur laquelle PostgreSQL refuse FOR UPDATE.
    commande = (
        Commande.objects.select_for_update(of=('self',))
        .select_related('panier', 'moyen_paiement', 'ticket')
        .get(pk=commande.pk)
    )
    panier = commande.panier
    if panier_items is None:
        panier_items = list(
            PanierItem.objects.filter(panier=panier).select_related('piece', 'piece__categorie', 'piece__sous_categorie')
        )
    if ticket is None:
        ticket = Ticket.objects.filter(commande=commande).first()

    totals = compute_checkout_totals(
        total=commande.total,
        moyen_paiement=moyen_paiement,
        appliquer_tva=appliquer_tva,
    )
    montant_tva = totals['tva']
    montant_timbre = totals['timbre']
    bareme = totals['bareme']
    total_a_payer = totals['total_a_payer']
    if montant_paye is None:
        montant_paye = total_a_payer
    montant_paye = Decimal(str(montant_paye))

    user = caissier
    if user is None and request is not None and getattr(request, 'user', None) and request.user.is_authenticated:
        user = request.user

    already_paid = commande.paye and panier.panier_paye
    if not already_paid:
        commande.moyen_paiement = moyen_paiement
        commande.paye = True
        commande.montant_tva = montant_tva
        commande.tva_appliquee = totals['tva_appliquee']
        commande.montant_timbre = montant_timbre
        commande.bareme_timbre = bareme
        if user is not None:
            commande.utilisateur = user
        commande.montant_paye = montant_paye
        commande.montant_reste = montant_paye - total_a_payer
        commande.save()

        if ticket:
            ticket.utilise = True
            if user is not None:
                ticket.utilisateur = user
            ticket.save()

        panier.panier_paye = True
        panier.date_paie_panier = timezone.now().date()
        panier.save()

        loc = getattr(panier, 'local_entrepot', None)
        if ticket:
            publish_paiement_valide(
                ticket.numero,
                panier.id,
                float(commande.total),
                commande.id,
                local_entrepot_id=loc.pk if loc else None,
                local_entrepot_nom=str(loc) if loc else None,
            )
        loc_paiement = loc
        if loc_paiement:
            from django.core.exceptions import ValidationError
            try:
                for item in panier_items:
                    decrementer_stock(item.piece, loc_paiement, item.quantite)
            except ValidationError as exc:
                raise ValueError(
                    '; '.join(exc.messages) if getattr(exc, 'messages', None) else str(exc)
                ) from exc

        if request is not None:
            try:
                from stock import printer_service
                if printer_service.HAS_USB:
                    printer_service.print_receipt_for_request(request, commande, panier_items)
            except Exception as exc:
                logger.warning('Impression thermique caisse: %s', exc)

        if ticket:
            try:
                from stock.receipt_ticket_pdf import save_receipt_pdf_file
                ticket.fichier_pdf = save_receipt_pdf_file(commande, panier_items)
                ticket.save(update_fields=['fichier_pdf'])
            except Exception as exc:
                logger.warning('PDF reçu caisse: %s', exc)

    bon_paiement = None
    try:
        bon_paiement = commande.bon_paiement
    except Exception:
        bon_paiement = None
    if bon_paiement is None and ticket:
        try:
            bon_paiement = creer_bon_commande_paiement(
                commande, panier, ticket, user or commande.utilisateur
            )
        except Exception as exc:
            logger.exception('Bon de commande caisse: %s', exc)
            bon_paiement = getattr(commande, 'bon_paiement', None)

    return {
        'commande': commande,
        'panier': panier,
        'ticket': ticket,
        'panier_items': panier_items,
        'bon_paiement': bon_paiement,
        'already_paid': already_paid,
    }
