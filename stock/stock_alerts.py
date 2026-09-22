"""
Alertes de stock par localité (StockLocal).
"""
from __future__ import annotations

from django.contrib.auth import get_user_model
from django.db import models
from django.utils import timezone

from .models import Piece, Notification, StockLocal

User = get_user_model()


def detecter_stocks_en_alerte():
    """Lignes StockLocal sous le seuil (pièce active)."""
    return list(
        StockLocal.objects.filter(
            quantite_disponible__lt=models.F('seuil_local'),
            seuil_local__gt=0,
            active_sortie=True,
            piece__active_sortie=True,
        ).select_related('piece', 'local_entrepot', 'piece__categorie')
    )


def _build_payload(stocks, creneau_label):
    n = len(stocks)
    horaire = f" ({creneau_label})" if creneau_label else ""
    if n == 1:
        s = stocks[0]
        msg = (
            f"{s.piece.designation} ({s.piece.numero_piece}) @ {s.local_entrepot.nom} : "
            f"stock {s.quantite_disponible} / seuil {s.seuil_local}"
        )
    else:
        parts = [
            f"{s.piece.designation} ({s.local_entrepot.nom})" for s in stocks[:5]
        ]
        lst = ", ".join(parts)
        if n > 5:
            lst += f" et {n - 5} autre(s)"
        msg = f"{n} alerte(s) stock : {lst}"
    titre = f"Alerte stock{horaire} — {n} ligne(s)"
    return titre, msg


def creer_notification_stock_alerte(creneau=None, force=False):
    stocks_alerte = detecter_stocks_en_alerte()
    if not stocks_alerte:
        return 0

    if creneau is None:
        creneau = get_active_creneau()
    if creneau is None:
        return 0

    cle_creneau = creneau['cle']
    creneau_label = creneau.get('label', '')

    utilisateurs_actifs = User.objects.filter(is_active=True)
    count = 0

    for utilisateur in utilisateurs_actifs:
        role = getattr(utilisateur, 'role', None)
        is_admin_like = utilisateur.is_superuser or role == 'admin'
        user_localite = getattr(utilisateur, 'local_entrepot', None)

        if is_admin_like:
            stocks_user = stocks_alerte
        elif user_localite:
            stocks_user = [s for s in stocks_alerte if s.local_entrepot_id == user_localite.pk]
        else:
            continue

        if not stocks_user:
            continue

        titre, message = _build_payload(stocks_user, creneau_label)
        pieces = [s.piece for s in stocks_user]

        existing = Notification.objects.filter(
            utilisateur=utilisateur,
            type_notification='stock_alerte',
            creneau=cle_creneau,
        ).first()

        if existing and not force:
            continue

        if existing:
            existing.titre = titre
            existing.message = message
            existing.lu = False
            existing.save()
            existing.pieces_alerte.set(pieces)
            count += 1
        else:
            notification = Notification.objects.create(
                type_notification='stock_alerte',
                titre=titre,
                message=message,
                utilisateur=utilisateur,
                creneau=cle_creneau,
                lu=False,
            )
            notification.pieces_alerte.set(pieces)
            count += 1

    return count


# Réexport des fonctions de planification (définies plus bas dans le fichier original)
from django.conf import settings
from datetime import time
from zoneinfo import ZoneInfo

DEFAULT_STOCK_ALERT_SLOTS = (
    (8, 0),
    (12, 0),
    (16, 0),
    (20, 0),
    (22, 0),
)


def _alert_timezone():
    tz_name = getattr(settings, 'STOCK_ALERT_TIMEZONE', None) or settings.TIME_ZONE
    try:
        return ZoneInfo(tz_name)
    except Exception:
        return timezone.get_current_timezone()


def get_stock_alert_slots():
    return getattr(settings, 'STOCK_ALERT_HOURS', DEFAULT_STOCK_ALERT_SLOTS)


def _local_now():
    return timezone.localtime(timezone.now(), _alert_timezone())


def get_active_creneau(now=None):
    now = now or _local_now()
    if timezone.is_naive(now):
        now = timezone.make_aware(now, _alert_timezone())
    slots = get_stock_alert_slots()
    active_idx = None
    for i, (hour, minute) in enumerate(slots):
        if now.time() >= time(hour, minute):
            active_idx = i
    if active_idx is None:
        return None
    hour, minute = slots[active_idx]
    return {
        'index': active_idx,
        'cle': f"{now.date().isoformat()}_{active_idx}",
        'label': f"{hour:02d}h{minute:02d}",
        'heure': time(hour, minute),
    }


def get_next_creneau(now=None):
    now = now or _local_now()
    slots = get_stock_alert_slots()
    for hour, minute in slots:
        if now.time() < time(hour, minute):
            return f"{hour:02d}h{minute:02d}"
    return f"{slots[0][0]:02d}h{slots[0][1]:02d} (demain)"


def process_stock_alert_schedule(force=False):
    from django.core.cache import cache

    creneau = get_active_creneau()
    if creneau is None and force:
        now = _local_now()
        creneau = {
            'index': -1,
            'cle': f"{now.date().isoformat()}_force",
            'label': 'immédiat',
            'heure': now.time(),
        }
    if creneau is None:
        return {'created': 0, 'creneau': None, 'pieces': len(detecter_stocks_en_alerte())}

    cache_key = 'stock_alert_last_creneau_sent'
    if not force and cache.get(cache_key) == creneau['cle']:
        return {
            'created': 0,
            'creneau': creneau,
            'pieces': len(detecter_stocks_en_alerte()),
            'skipped': True,
        }

    created = creer_notification_stock_alerte(creneau=creneau, force=force)
    cache.set(cache_key, creneau['cle'], timeout=60 * 60 * 6)
    return {
        'created': created,
        'creneau': creneau,
        'pieces': len(detecter_stocks_en_alerte()),
        'skipped': False,
    }
