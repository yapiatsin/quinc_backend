"""Vues GeniusPay : webhook, retour caisse, statut de paiement caisse."""
from __future__ import annotations

import json
import logging

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_GET, require_POST

from stock.geniuspay import GeniusPayError, verifier_signature_webhook
from stock.models import Commande, GeniusPayPaiement, Panier, PanierItem, Ticket
from stock.paiement_service import (
    appliquer_confirmation_geniuspay,
    payload_succes_caisse,
    synchroniser_paiement,
)

logger = logging.getLogger(__name__)


def _caisse_panier_ticket(request, ticket_id):
    from stock.views import get_user_localite
    localite = get_user_localite(request.user)
    filtre_panier = {
        'ticket': ticket_id,
        'valide': True,
        'commande_en_ligne': False,
    }
    if localite:
        filtre_panier['local_entrepot'] = localite
    panier = get_object_or_404(Panier, **filtre_panier)
    ticket = get_object_or_404(Ticket, numero=ticket_id)
    return panier, ticket, ticket.commande


def _json_succes_caisse(request, commande, panier, ticket):
    panier_items = list(
        PanierItem.objects.filter(panier=panier).select_related('piece', 'piece__categorie', 'piece__sous_categorie')
    )
    bon = None
    try:
        bon = commande.bon_paiement
    except Exception:
        bon = None
    return JsonResponse(
        payload_succes_caisse(request, commande, panier, panier_items, ticket, bon)
    )


@login_required(login_url='connexion')
@require_GET
def caisse_geniuspay_statut(request, ticket_id):
    panier, ticket, commande = _caisse_panier_ticket(request, ticket_id)
    if commande.paye and panier.panier_paye:
        return _json_succes_caisse(request, commande, panier, ticket)

    gp = (
        GeniusPayPaiement.objects.filter(
            commande=commande,
            source=GeniusPayPaiement.SOURCE_CAISSE,
        )
        .order_by('-date_creation')
        .first()
    )
    if not gp:
        return JsonResponse(
            {'success': False, 'pending': True, 'error': 'Aucun paiement GeniusPay en cours.'},
            status=404,
        )
    try:
        gp, confirmed = synchroniser_paiement(gp, request=request)
    except GeniusPayError as exc:
        return JsonResponse({'success': False, 'pending': True, 'error': str(exc)}, status=400)

    commande.refresh_from_db()
    panier.refresh_from_db()
    if confirmed or commande.paye:
        return _json_succes_caisse(request, commande, panier, ticket)
    if gp.statut in ('failed', 'cancelled', 'expired'):
        return JsonResponse({
            'success': False,
            'failed': True,
            'pending': False,
            'error': 'Le paiement GeniusPay a échoué ou a été annulé.',
        })
    return JsonResponse({
        'success': True,
        'pending': True,
        'reference': gp.reference,
        'status': gp.statut,
    })


@login_required(login_url='connexion')
def caisse_geniuspay_retour(request):
    ticket_id = (request.GET.get('ticket') or '').strip()
    status = (request.GET.get('status') or '').strip().lower()
    commande_id = (request.GET.get('commande') or '').strip()
    error = status in ('error', 'failed', 'echec')

    commande = None
    ticket = None
    if ticket_id:
        ticket = Ticket.objects.filter(numero=ticket_id).select_related('commande', 'commande__panier').first()
        if ticket:
            commande = ticket.commande
    elif commande_id:
        commande = Commande.objects.filter(pk=commande_id).select_related('panier').first()
        ticket = Ticket.objects.filter(commande=commande).first() if commande else None

    paye = False
    if commande and not error:
        gp = (
            GeniusPayPaiement.objects.filter(commande=commande)
            .order_by('-date_creation')
            .first()
        )
        if gp:
            try:
                gp, confirmed = synchroniser_paiement(gp, request=request)
                commande.refresh_from_db()
                paye = commande.paye or confirmed
            except GeniusPayError as exc:
                messages.error(request, str(exc))
                error = True
        else:
            paye = commande.paye

    return render(request, 'mag/geniuspay_retour.html', {
        'commande': commande,
        'ticket': ticket,
        'paye': paye,
        'error': error,
        'caisse_url': reverse('caissiere'),
    })


@csrf_exempt
@require_POST
def geniuspay_webhook(request):
    signature = request.headers.get('X-Webhook-Signature') or ''
    timestamp = request.headers.get('X-Webhook-Timestamp') or ''
    event = request.headers.get('X-Webhook-Event') or ''
    raw = request.body or b''

    if not verifier_signature_webhook(raw, timestamp, signature):
        return JsonResponse(
            {'status': 401, 'detail': 'Invalid signature'},
            status=401,
        )

    try:
        payload = json.loads(raw.decode('utf-8') or '{}')
    except ValueError:
        return JsonResponse({'status': 400, 'detail': 'Invalid JSON'}, status=400)

    event = event or payload.get('event') or ''
    data = payload.get('data') or {}
    reference = data.get('reference') or ''
    metadata = data.get('metadata') or {}
    commande_id = str(metadata.get('commande_id') or '')

    gp = None
    if reference:
        gp = GeniusPayPaiement.objects.filter(reference=reference).first()
    if gp is None and commande_id:
        gp = (
            GeniusPayPaiement.objects.filter(commande_id=commande_id)
            .order_by('-date_creation')
            .first()
        )
    if gp is None:
        logger.warning('Webhook GeniusPay sans paiement local ref=%s', reference)
        return JsonResponse({'status': 'ignored'})

    if event in ('payment.failed', 'payment.cancelled', 'payment.expired'):
        statut_map = {
            'payment.failed': 'failed',
            'payment.cancelled': 'cancelled',
            'payment.expired': 'expired',
        }
        gp.statut = statut_map.get(event, gp.statut)
        gp.raw_response = data if isinstance(data, dict) else gp.raw_response
        gp.save(update_fields=['statut', 'raw_response'])
        return JsonResponse({'status': 'ok'})

    if event == 'payment.success' or data.get('status') == 'completed':
        try:
            data_ok = dict(data)
            data_ok.setdefault('status', 'completed')
            data_ok.setdefault('amount', gp.amount)
            appliquer_confirmation_geniuspay(gp, data_ok, request=None)
        except GeniusPayError:
            logger.exception('Webhook GeniusPay confirmation ref=%s', gp.reference)
            return JsonResponse({'status': 400, 'detail': 'Confirm failed'}, status=400)
        except Exception:
            logger.exception('Webhook GeniusPay erreur ref=%s', gp.reference)
            return JsonResponse({'status': 500, 'detail': 'Server error'}, status=500)
    return JsonResponse({'status': 'ok'})
