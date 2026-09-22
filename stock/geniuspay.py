"""Client HTTP GeniusPay (clés uniquement côté serveur)."""
from __future__ import annotations

import hashlib
import hmac
import logging
import re
import time
from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path

import requests
from django.conf import settings

logger = logging.getLogger(__name__)

TIMEOUT = 30
WEBHOOK_MAX_AGE_SECONDS = 300

_MESSAGES_ERREUR = {
    'INVALID_API_KEY': (
        'Clé API GeniusPay invalide. Vérifiez GENIUSPAY_API_KEY et '
        'GENIUSPAY_API_SECRET dans le fichier .env (Sandbox vs Production).'
    ),
    'MISSING_API_KEY': (
        'Clé API GeniusPay manquante. Renseignez GENIUSPAY_API_KEY et '
        'GENIUSPAY_API_SECRET dans le fichier .env.'
    ),
    'MERCHANT_INACTIVE': 'Compte marchand GeniusPay désactivé.',
    'PAYMENT_INIT_FAILED': (
        'GeniusPay n’a pas pu initialiser le paiement. '
        'Réessayez, ou vérifiez les clés API / l’URL de retour (PUBLIC_BASE_URL).'
    ),
    'VALIDATION_ERROR': 'Données de paiement invalides pour GeniusPay.',
    'COUNTRY_NOT_SUPPORTED': 'Pays non supporté par GeniusPay.',
}


class GeniusPayError(Exception):
    def __init__(self, message, code=None, http_status=None):
        super().__init__(message)
        self.code = code
        self.http_status = http_status


def _env(name, default=''):
    """Lit le .env à chaque appel (le runserver ne recharge pas .env tout seul)."""
    env_file = Path(settings.BASE_DIR) / '.env'
    if env_file.is_file():
        try:
            from decouple import Config, RepositoryEnv
            value = Config(RepositoryEnv(str(env_file)))(name, default=default)
            if value not in (None, ''):
                return str(value).strip()
        except Exception:
            pass
    return str(getattr(settings, name, default) or default).strip()


def _headers():
    api_key = _env('GENIUSPAY_API_KEY')
    api_secret = _env('GENIUSPAY_API_SECRET')
    if not api_key or not api_secret:
        raise GeniusPayError(
            _MESSAGES_ERREUR['MISSING_API_KEY'],
            code='MISSING_API_KEY',
            http_status=401,
        )
    return {
        'X-API-Key': api_key,
        'X-API-Secret': api_secret,
        'Content-Type': 'application/json',
        'Accept': 'application/json',
    }


def _base_url():
    return (
        _env('GENIUSPAY_BASE_URL', 'https://pay.genius.ci/api/v1/merchant')
        or 'https://pay.genius.ci/api/v1/merchant'
    ).rstrip('/')


def montant_xof(value) -> int:
    """Montant entier en XOF (l'API GeniusPay n'accepte pas les centimes)."""
    return int(Decimal(str(value or 0)).quantize(Decimal('1'), rounding=ROUND_HALF_UP))


def sanitize_customer(customer: dict | None) -> dict | None:
    """Nettoie le client : téléphone CI valide, champs vides exclus."""
    if not customer:
        return None
    cleaned = {}
    name = str(customer.get('name') or '').strip()[:120]
    email = str(customer.get('email') or '').strip()[:120]
    phone = str(customer.get('phone') or '').strip()
    country = str(customer.get('country') or 'CI').strip().upper()[:2] or 'CI'
    if name:
        cleaned['name'] = name
    if email and '@' in email:
        cleaned['email'] = email
    phone_ok = normalize_phone_for_api(phone)
    if phone_ok:
        cleaned['phone'] = phone_ok
    cleaned['country'] = country
    return cleaned or None


def normalize_phone_for_api(raw: str) -> str:
    """
    Retourne un téléphone international utilisable, ou '' si invalide.
    CI : +225 suivi de 10 chiffres (ex. +2250701234567).
    """
    raw = (raw or '').strip()
    if not raw:
        return ''
    digits = re.sub(r'\D', '', raw)
    if not digits:
        return ''
    # CI : 225 + 10 chiffres
    if digits.startswith('225'):
        local = digits[3:]
        if len(local) == 10:
            return '+225' + local
        if len(local) == 8:
            return '+22507' + local
        if len(local) > 10:
            return '+225' + local[-10:]
        return ''
    if len(digits) == 10 and digits.startswith(('0', '1', '5', '7')):
        return '+225' + digits
    if len(digits) == 8:
        return '+22507' + digits
    # Autre pays : E.164 basique
    if raw.startswith('+') and 10 <= len(digits) <= 15:
        return '+' + digits
    return ''


def _sanitize_metadata(metadata: dict | None) -> dict | None:
    if not metadata:
        return None
    out = {}
    for key, value in metadata.items():
        k = str(key)[:64]
        if value is None:
            continue
        if isinstance(value, (int, float, bool)):
            out[k] = value
        else:
            # Évite les caractères exotiques (ex. N°) qui font échouer certains gateways
            text = str(value).replace('\u00b0', '').replace('°', '')
            text = re.sub(r'[^\w\s\-./:@+]', '', text, flags=re.UNICODE).strip()
            if text:
                out[k] = text[:200]
    return out or None


def _parse_error(payload, http_status):
    if not isinstance(payload, dict):
        return GeniusPayError(
            f'Réponse GeniusPay invalide (HTTP {http_status}).',
            http_status=http_status,
        )
    err = payload.get('error') or {}
    code = None
    message = None
    if isinstance(err, dict):
        code = err.get('code')
        message = err.get('message') or err.get('detail')
    elif err:
        message = str(err)
    if not message:
        message = payload.get('message') or payload.get('detail')
    if code in _MESSAGES_ERREUR:
        message = _MESSAGES_ERREUR[code]
    elif not message or message.strip().lower() in (
        'erreur geniuspay.',
        'erreur geniuspay',
        'error',
    ):
        if code:
            message = f'Erreur GeniusPay ({code}).'
        else:
            message = f'Erreur GeniusPay (HTTP {http_status}).'
    elif code and code not in str(message):
        message = f'{message} [{code}]'
    return GeniusPayError(message, code=code, http_status=http_status)


def _request(method, path, *, json_body=None, params=None):
    url = f"{_base_url()}{path}"
    try:
        response = requests.request(
            method,
            url,
            headers=_headers(),
            json=json_body,
            params=params,
            timeout=TIMEOUT,
        )
    except requests.RequestException as exc:
        logger.exception('GeniusPay réseau: %s', exc)
        raise GeniusPayError(
            'Impossible de joindre GeniusPay. Réessayez dans un instant.',
        ) from exc

    try:
        payload = response.json()
    except ValueError:
        payload = {}

    if response.status_code >= 400 or (
        isinstance(payload, dict) and payload.get('success') is False
    ):
        logger.warning(
            'GeniusPay erreur %s %s body=%s payload=%s',
            method,
            url,
            (response.text or '')[:500],
            payload,
        )
        raise _parse_error(payload, response.status_code)

    if isinstance(payload, dict) and 'data' in payload:
        return payload['data']
    return payload


def initier_paiement(
    amount,
    *,
    description='',
    customer=None,
    metadata=None,
    success_url=None,
    error_url=None,
    currency='XOF',
):
    """
    Crée un paiement en mode checkout (sans payment_method).
    Retourne data: id, reference, checkout_url, status, ...
    """
    montant = montant_xof(amount)
    min_amount = int(getattr(settings, 'GENIUSPAY_MIN_AMOUNT', 200) or 200)
    if montant < min_amount:
        raise GeniusPayError(
            f'Le montant minimum GeniusPay est de {min_amount} FCFA.',
            code='VALIDATION_ERROR',
            http_status=422,
        )
    description = re.sub(
        r'[^\w\s\-./]',
        '',
        (description or '').replace('\u00b0', '').replace('°', ''),
        flags=re.UNICODE,
    ).strip()[:500]

    body = {
        'amount': montant,
        'currency': currency or 'XOF',
        'description': description or 'Paiement Auto-Piece',
    }
    customer_clean = sanitize_customer(customer)
    if customer_clean:
        body['customer'] = customer_clean
    meta_clean = _sanitize_metadata(metadata)
    if meta_clean:
        body['metadata'] = meta_clean
    if success_url:
        body['success_url'] = str(success_url)[:500]
    if error_url:
        body['error_url'] = str(error_url)[:500]

    try:
        return _request('POST', '/payments', json_body=body)
    except GeniusPayError as exc:
        # Second essai minimal : certains comptes sandbox rejettent customer/metadata
        retryable = exc.code in (
            'PAYMENT_INIT_FAILED',
            'VALIDATION_ERROR',
            None,
        ) or exc.http_status in (400, 422)
        if not retryable:
            raise
        minimal = {
            'amount': montant,
            'currency': currency or 'XOF',
            'description': body['description'],
        }
        if success_url:
            minimal['success_url'] = str(success_url)[:500]
        if error_url:
            minimal['error_url'] = str(error_url)[:500]
        if set(minimal.keys()) == set(body.keys()) and all(
            minimal.get(k) == body.get(k) for k in minimal
        ):
            raise
        logger.warning(
            'GeniusPay retry payload minimal après %s: %s',
            exc.code or exc.http_status,
            exc,
        )
        return _request('POST', '/payments', json_body=minimal)


def recuperer_paiement(reference: str):
    if not reference:
        raise GeniusPayError(
            'Référence de paiement manquante.',
            code='VALIDATION_ERROR',
            http_status=422,
        )
    ref = str(reference).strip()
    return _request('GET', f'/payments/{ref}')


def verifier_signature_webhook(raw_body: bytes, timestamp: str, signature: str) -> bool:
    """
    signature = HMAC-SHA256(timestamp + '.' + json_payload, secret)
    Utilise le corps brut, sans ré-encoder le JSON.
    """
    secret = _env('GENIUSPAY_WEBHOOK_SECRET')
    if not secret:
        logger.warning('GENIUSPAY_WEBHOOK_SECRET vide : webhook rejeté.')
        return False
    if not timestamp or not signature:
        return False
    try:
        ts = int(str(timestamp).strip())
    except (TypeError, ValueError):
        return False
    if abs(int(time.time()) - ts) > WEBHOOK_MAX_AGE_SECONDS:
        return False
    if isinstance(raw_body, bytes):
        payload = raw_body.decode('utf-8')
    else:
        payload = str(raw_body or '')
    expected = hmac.new(
        secret.encode('utf-8'),
        f'{ts}.{payload}'.encode('utf-8'),
        hashlib.sha256,
    ).hexdigest()
    provided = str(signature).strip()
    if provided.lower().startswith('sha256='):
        provided = provided.split('=', 1)[1]
    if len(provided) != len(expected):
        return False
    return hmac.compare_digest(expected, provided)
