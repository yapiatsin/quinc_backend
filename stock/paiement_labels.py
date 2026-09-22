"""Libellés de paiement (Espèce vs opérateur GeniusPay : Wave, Orange Money, …)."""
from __future__ import annotations

LABELS_PAYMENT_METHOD = {
    'wave': 'Wave',
    'orange_money': 'Orange Money',
    'orange': 'Orange Money',
    'mtn_money': 'MTN MoMo',
    'mtn_momo': 'MTN MoMo',
    'mtn': 'MTN MoMo',
    'moov_money': 'Moov Money',
    'moov': 'Moov Money',
    'airtel_money': 'Airtel Money',
    'airtel': 'Airtel Money',
    'card': 'Carte bancaire',
    'carte': 'Carte bancaire',
    'visa': 'Carte bancaire',
    'mastercard': 'Carte bancaire',
    'paystack': 'Carte bancaire',
    'pawapay': 'Mobile Money',
    'mobile_money': 'Mobile Money',
}

_MMO_PREFIXES = (
    ('ORANGE', 'Orange Money'),
    ('MTN', 'MTN MoMo'),
    ('MOOV', 'Moov Money'),
    ('AIRTEL', 'Airtel Money'),
    ('WAVE', 'Wave'),
    ('FREE', 'Free Money'),
    ('MPESA', 'M-Pesa'),
    ('VODACOM', 'M-Pesa'),
)

_GENERIC_METHODS = frozenset({
    'pawapay',
    'paystack',
    'mobile_money',
    'geniuspay',
    'checkout',
})


def _normalize_code(value) -> str:
    if value is None:
        return ''
    if isinstance(value, (int, float)):
        return str(value).strip()
    if not isinstance(value, str):
        return ''
    return value.strip()


def label_canal_paiement(code: str) -> str:
    raw = _normalize_code(code)
    if not raw:
        return ''
    key = raw.lower().replace('-', '_').replace(' ', '_')
    if key in LABELS_PAYMENT_METHOD:
        return LABELS_PAYMENT_METHOD[key]
    upper = raw.upper().replace('-', '_')
    for prefix, label in _MMO_PREFIXES:
        if upper.startswith(prefix):
            return label
    return raw.replace('_', ' ').title()


def extraire_canal_depuis_payload(data: dict | None) -> tuple[str, str]:
    """Retourne (code, libellé) depuis une réponse GET/webhook GeniusPay."""
    if not isinstance(data, dict):
        return '', ''
    payload = data
    inner = data.get('data')
    if isinstance(inner, dict) and any(
        k in inner
        for k in ('payment_method', 'payment_provider', 'provider', 'gateway', 'mmo_provider')
    ):
        payload = inner

    candidates = [
        payload.get('payment_provider'),
        payload.get('provider'),
        payload.get('mmo_provider'),
        payload.get('gateway'),
        payload.get('payment_method'),
        payload.get('method'),
        payload.get('channel'),
        payload.get('operator'),
    ]
    generic_code, generic_label = '', ''
    for cand in candidates:
        code = _normalize_code(cand)
        if not code:
            continue
        label = label_canal_paiement(code)
        if not label:
            continue
        key = code.lower().replace('-', '_').replace(' ', '_')
        if key in _GENERIC_METHODS:
            if not generic_code:
                generic_code, generic_label = code, label
            continue
        return code, label
    return generic_code, generic_label


def libelle_canal_paiement(gp) -> str:
    if gp is None:
        return ''
    stored = _normalize_code(getattr(gp, 'payment_method', ''))
    stored_label = label_canal_paiement(stored) if stored else ''
    stored_key = stored.lower().replace('-', '_').replace(' ', '_')
    if stored_label and stored_key not in _GENERIC_METHODS:
        return stored_label

    data = getattr(gp, 'raw_response', None)
    _code, from_raw = extraire_canal_depuis_payload(data if isinstance(data, dict) else {})
    if from_raw:
        return from_raw
    return stored_label


def _paiement_geniuspay_commande(commande):
    qs = getattr(commande, 'paiements_geniuspay', None)
    if qs is None:
        return None
    cache = getattr(commande, '_prefetched_objects_cache', None) or {}
    if 'paiements_geniuspay' in cache:
        paiements = list(qs.all())
        return next((p for p in paiements if p.statut == 'completed'), None) or (
            paiements[0] if paiements else None
        )
    return (
        qs.filter(statut='completed').order_by('-date_maj').first()
        or qs.order_by('-date_creation').first()
    )


def libelle_moyen_paiement_commande(commande) -> str:
    """Libellé ticket : Espèce, ou Paiement numérique (Wave), etc."""
    moyen = getattr(commande, 'moyen_paiement', None)
    nom = (getattr(moyen, 'nom', None) or '').strip() or '—'
    code = (getattr(moyen, 'code', '') or '').lower()
    if code != 'geniuspay':
        return nom
    canal = libelle_canal_paiement(_paiement_geniuspay_commande(commande))
    if canal:
        return f'{nom} ({canal})'
    return nom
