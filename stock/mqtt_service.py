# stock/mqtt_service.py — couche temps réel (Pusher ; remplace MQTT local)
"""
API publique inchangée pour les callers (views / ecom / transferts).
Canaux Pusher :
  magasin-panier           event: valide
  magasin-paiement         event: valide
  magasin-transfert        event: demande
  magasin-commande-ligne   event: update
  magasin-chat             event: typing
"""
import logging
import threading

from decouple import config
from django.conf import settings

logger = logging.getLogger(__name__)

CHANNEL_PANIER = "magasin-panier"
CHANNEL_PAIEMENT = "magasin-paiement"
CHANNEL_TRANSFERT = "magasin-transfert"
CHANNEL_CMD_LIGNE = "magasin-commande-ligne"
CHANNEL_CHAT = "magasin-chat"

_client = None
_client_lock = threading.Lock()
_client_creds = None  # (app_id, key, secret, cluster) used to build _client


def _pusher_credentials():
    """Lit les credentials à chaque appel (évite un snapshot settings vide)."""
    app_id = (config('PUSHER_APP_ID', default='') or getattr(settings, 'PUSHER_APP_ID', '') or '').strip()
    key = (config('PUSHER_KEY', default='') or getattr(settings, 'PUSHER_KEY', '') or '').strip()
    secret = (config('PUSHER_SECRET', default='') or getattr(settings, 'PUSHER_SECRET', '') or '').strip()
    cluster = (config('PUSHER_CLUSTER', default='') or getattr(settings, 'PUSHER_CLUSTER', '') or 'eu').strip() or 'eu'
    return app_id, key, secret, cluster


def get_pusher_client():
    """Retourne un client Pusher singleton, ou None si non configuré."""
    global _client, _client_creds

    creds = _pusher_credentials()
    app_id, key, secret, cluster = creds

    with _client_lock:
        if _client is not None and _client_creds == creds:
            return _client

        if not (app_id and key and secret):
            logger.warning(
                "Pusher non configuré (PUSHER_APP_ID / KEY / SECRET manquants) — publish no-op"
            )
            _client = None
            _client_creds = None
            return None

        try:
            import pusher

            _client = pusher.Pusher(
                app_id=app_id,
                key=key,
                secret=secret,
                cluster=cluster,
                ssl=True,
            )
            _client_creds = creds
            logger.info("Client Pusher initialisé (cluster=%s, app_id=%s)", cluster, app_id)
        except Exception as e:
            logger.error("Erreur initialisation Pusher: %s", e)
            _client = None
            _client_creds = None
            return None

    return _client


def _trigger(channel, event, data):
    """Déclenche un événement Pusher ; ne fait jamais planter la requête métier."""
    try:
        client = get_pusher_client()
        if client is None:
            return False
        client.trigger(channel, event, data)
        logger.info("Pusher %s/%s publié", channel, event)
        return True
    except Exception as e:
        logger.error("Erreur publication Pusher %s/%s: %s", channel, event, e)
        return False


def publish_panier_valide(panier_id, total, local_entrepot_id=None, local_entrepot_nom=None):
    """Notifie la caisse qu'un panier a été validé."""
    _trigger(
        CHANNEL_PANIER,
        "valide",
        {
            "panier_id": str(panier_id),
            "total": float(total),
            "local_entrepot_id": str(local_entrepot_id) if local_entrepot_id is not None else None,
            "local_entrepot_nom": local_entrepot_nom,
        },
    )


def publish_paiement_valide(
    ticket_id, panier_id, total, commande_id, local_entrepot_id=None, local_entrepot_nom=None
):
    """Notifie la page livraisons qu'un paiement a été validé."""
    _trigger(
        CHANNEL_PAIEMENT,
        "valide",
        {
            "ticket_id": str(ticket_id),
            "panier_id": str(panier_id),
            "total": float(total),
            "commande_id": str(commande_id),
            "local_entrepot_id": str(local_entrepot_id) if local_entrepot_id is not None else None,
            "local_entrepot_nom": local_entrepot_nom,
        },
    )


def publish_transfert_demande(
    demande_id,
    numero_demande,
    event,
    local_donneur_id=None,
    local_demandeur_id=None,
    statut=None,
):
    """Notifie la page transferts d'un changement de demande."""
    _trigger(
        CHANNEL_TRANSFERT,
        "demande",
        {
            "demande_id": demande_id,
            "numero_demande": numero_demande,
            "event": event,
            "local_donneur_id": str(local_donneur_id) if local_donneur_id else None,
            "local_demandeur_id": str(local_demandeur_id) if local_demandeur_id else None,
            "statut": statut,
        },
    )


def publish_chat_typing(conversation_id, who, typing, label=None):
    """Notifie le chat qu'un participant est en train d'écrire."""
    _trigger(
        CHANNEL_CHAT,
        "typing",
        {
            "conversation_id": int(conversation_id),
            "who": who,
            "typing": bool(typing),
            "label": label,
        },
    )


def publish_commande_ligne(
    event,
    commande_id,
    numero_commande,
    *,
    total=None,
    statut=None,
    local_entrepot_id=None,
    local_entrepot_nom=None,
    livreur_id=None,
    paye=None,
):
    """
    Notifie les pages magasin (cmd_line / livraison_cmd_online / list_cmd).

    event: nouvelle | valider | assigner | payer | livrer | annuler
    """
    _trigger(
        CHANNEL_CMD_LIGNE,
        "update",
        {
            "event": event,
            "commande_id": str(commande_id),
            "numero_commande": numero_commande,
            "total": float(total) if total is not None else None,
            "statut": statut,
            "local_entrepot_id": str(local_entrepot_id) if local_entrepot_id else None,
            "local_entrepot_nom": local_entrepot_nom,
            "livreur_id": str(livreur_id) if livreur_id else None,
            "paye": bool(paye) if paye is not None else None,
        },
    )
