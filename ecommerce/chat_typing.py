"""Indicateur « en train d'écrire » pour le chat e-commerce (BDD + Pusher)."""

from __future__ import annotations

from datetime import timedelta

from django.utils import timezone

from .models import ChatConversation

TYPING_TTL = timedelta(seconds=4)


def _typing_at(conversation: ChatConversation, role: str):
    if role == 'client':
        return conversation.client_typing_at
    if role == 'staff':
        return conversation.staff_typing_at
    return None


def set_typing(conversation: ChatConversation, role: str) -> None:
    now = timezone.now()
    field = 'client_typing_at' if role == 'client' else 'staff_typing_at'
    ChatConversation.objects.filter(pk=conversation.pk).update(**{field: now})
    setattr(conversation, field, now)


def clear_typing(conversation: ChatConversation, role: str) -> None:
    field = 'client_typing_at' if role == 'client' else 'staff_typing_at'
    ChatConversation.objects.filter(pk=conversation.pk).update(**{field: None})
    setattr(conversation, field, None)


def is_typing(conversation: ChatConversation, role: str) -> bool:
    at = _typing_at(conversation, role)
    if at is None:
        return False
    return timezone.now() - at < TYPING_TTL


def typing_payload_for_viewer(conversation: ChatConversation, viewer_role: str) -> dict:
    """viewer_role: 'client' ou 'staff'."""
    other_role = 'staff' if viewer_role == 'client' else 'client'
    if not is_typing(conversation, other_role):
        return {'typing': False, 'typing_label': None}
    label = 'Conseiller' if other_role == 'staff' else 'Client'
    return {'typing': True, 'typing_label': f'{label} écrit…'}


def publish_typing_event(
    conversation_id: int,
    *,
    who: str,
    typing: bool,
    label: str | None = None,
) -> None:
    try:
        from stock.mqtt_service import publish_chat_typing

        publish_chat_typing(
            conversation_id=conversation_id,
            who=who,
            typing=typing,
            label=label,
        )
    except Exception:
        pass
