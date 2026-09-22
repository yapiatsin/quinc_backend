"""Service chatbot e-commerce : conversations client ↔ staff."""

from __future__ import annotations

from datetime import date, timedelta

from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from Userauths.models import CustomUser
from stock.models import Notification

from .chat_typing import (
    clear_typing,
    publish_typing_event,
    set_typing,
    typing_payload_for_viewer,
)
from .models import ChatConversation, ChatMessage
from .services import ROLES_STAFF_CMD, get_local_from_session

OPEN_STATUSES = (
    ChatConversation.STATUS_PENDING,
    ChatConversation.STATUS_ACTIVE,
)

IDLE_CLOSE_MINUTES = 5
CLIENT_HIDE_MESSAGES_MINUTES = 5
SESSION_DISMISS_KEY = 'ecom_chat_dismissed_id'

REFUSE_CLIENT_MESSAGE = (
    "Un conseiller n'est pas disponible pour le moment. "
    "Vous pouvez contacter le centre d'assistance, nous écrire via le formulaire de contact, "
    "ou nous joindre sur WhatsApp."
)

PENDING_CLIENT_MESSAGE = (
    "Un conseiller va prendre en charge votre demande. Merci de patienter…"
)

ACCEPT_SYSTEM_MESSAGE = "Un conseiller a rejoint la conversation."
CLOSE_SYSTEM_MESSAGE = "La conversation est terminée."


def _ensure_session_key(request) -> str:
    if not request.session.session_key:
        request.session.create()
    return request.session.session_key or ''


def _client_user(request):
    user = getattr(request, 'user', None)
    if user is not None and user.is_authenticated and getattr(user, 'role', None) in (
        'client',
        'admin',
    ):
        return user
    return None


def _activity_at(conversation: ChatConversation):
    return conversation.last_message_at or conversation.created_at


def _client_messages_hidden(conversation: ChatConversation) -> bool:
    if conversation.status != ChatConversation.STATUS_CLOSED or not conversation.closed_at:
        return False
    return timezone.now() >= conversation.closed_at + timedelta(minutes=CLIENT_HIDE_MESSAGES_MINUTES)


@transaction.atomic
def _close_conversation(conversation: ChatConversation, staff_user=None) -> ChatConversation:
    """Clôture une conversation (manuel ou idle)."""
    if conversation.status == ChatConversation.STATUS_CLOSED:
        return conversation
    now = timezone.now()
    conversation.status = ChatConversation.STATUS_CLOSED
    conversation.closed_at = now
    updates = ['status', 'closed_at', 'last_message_at', 'updated_at']
    if staff_user is not None:
        conversation.staff = staff_user
        updates.append('staff')
    conversation.last_message_at = now
    conversation.save(update_fields=updates)
    ChatMessage.objects.create(
        conversation=conversation,
        sender_type=ChatMessage.SENDER_SYSTEM,
        sender=staff_user,
        body=CLOSE_SYSTEM_MESSAGE,
    )
    return conversation


def maybe_auto_close_idle(conversation: ChatConversation | None) -> ChatConversation | None:
    """Ferme pending/active si aucune activité depuis IDLE_CLOSE_MINUTES."""
    if conversation is None:
        return None
    if conversation.status not in OPEN_STATUSES:
        return conversation
    activity = _activity_at(conversation)
    if activity is None:
        return conversation
    if timezone.now() - activity < timedelta(minutes=IDLE_CLOSE_MINUTES):
        return conversation
    return _close_conversation(conversation, staff_user=conversation.staff)


def get_or_create_open_conversation(request) -> ChatConversation:
    """Retourne la conversation ouverte (pending/active) du client ou invité."""
    client = _client_user(request)
    session_key = _ensure_session_key(request)

    qs = ChatConversation.objects.filter(status__in=OPEN_STATUSES)
    if client:
        conv = qs.filter(client=client).order_by('-last_message_at', '-created_at').first()
        if conv:
            conv = maybe_auto_close_idle(conv)
            if conv.status in OPEN_STATUSES:
                return conv
        return ChatConversation.objects.create(
            client=client,
            session_key=session_key,
            status=ChatConversation.STATUS_PENDING,
            handler_mode=ChatConversation.HANDLER_STAFF,
        )

    conv = (
        qs.filter(session_key=session_key, client__isnull=True)
        .order_by('-last_message_at', '-created_at')
        .first()
    )
    if conv:
        conv = maybe_auto_close_idle(conv)
        if conv.status in OPEN_STATUSES:
            return conv
    return ChatConversation.objects.create(
        session_key=session_key,
        status=ChatConversation.STATUS_PENDING,
        handler_mode=ChatConversation.HANDLER_STAFF,
    )


def get_current_conversation(request) -> ChatConversation | None:
    """Conversation ouverte ou dernière refusée/fermée pour affichage client."""
    client = _client_user(request)
    session_key = _ensure_session_key(request)
    qs = ChatConversation.objects.all()
    if client:
        conv = qs.filter(client=client).order_by('-last_message_at', '-created_at').first()
    else:
        conv = (
            qs.filter(session_key=session_key)
            .filter(Q(client__isnull=True) | Q(client=client))
            .order_by('-last_message_at', '-created_at')
            .first()
        )
    if conv:
        conv = maybe_auto_close_idle(conv)
    dismissed_id = request.session.get(SESSION_DISMISS_KEY)
    if (
        conv
        and dismissed_id
        and conv.pk == dismissed_id
        and conv.status == ChatConversation.STATUS_CLOSED
    ):
        return None
    if conv and conv.status in OPEN_STATUSES and dismissed_id:
        request.session.pop(SESSION_DISMISS_KEY, None)
    return conv


def serialize_message(msg: ChatMessage) -> dict:
    return {
        'id': msg.pk,
        'sender_type': msg.sender_type,
        'body': msg.body,
        'created_at': msg.created_at.isoformat() if msg.created_at else None,
    }


def serialize_conversation(
    conv: ChatConversation,
    *,
    include_messages: bool = True,
    for_client: bool = False,
    viewer_role: str | None = None,
) -> dict:
    data = {
        'id': conv.pk,
        'status': conv.status,
        'handler_mode': conv.handler_mode,
        'subject': conv.subject,
        'last_message_at': conv.last_message_at.isoformat() if conv.last_message_at else None,
        'closed_at': conv.closed_at.isoformat() if conv.closed_at else None,
        'created_at': conv.created_at.isoformat() if conv.created_at else None,
        'staff_name': (
            (conv.staff.get_full_name() or conv.staff.username) if conv.staff_id else None
        ),
        'client_label': _client_label(conv),
        'show_chips': conv.status == ChatConversation.STATUS_REFUSED,
        'can_start_new': conv.status == ChatConversation.STATUS_CLOSED,
    }
    if include_messages:
        if for_client and _client_messages_hidden(conv):
            data['messages'] = []
            data['messages_hidden'] = True
        else:
            data['messages'] = [
                serialize_message(m) for m in conv.messages.order_by('created_at')
            ]
            data['messages_hidden'] = False
    if viewer_role in ('client', 'staff'):
        conv.refresh_from_db(fields=['client_typing_at', 'staff_typing_at'])
        data.update(typing_payload_for_viewer(conv, viewer_role))
    return data


def _client_label(conv: ChatConversation) -> str:
    if conv.client_id:
        user = conv.client
        return (user.get_full_name() or user.username or user.email or f'Client #{user.pk}')
    if conv.session_key:
        return f'Invité ({conv.session_key[:8]}…)'
    return 'Invité'


def notify_staff_new_chat(conversation: ChatConversation, preview: str, request=None) -> None:
    """Notifie le staff (accueil / gestionnaire / chefagence / admin)."""
    local = get_local_from_session(request) if request is not None else None
    staff_qs = CustomUser.objects.filter(
        role__in=ROLES_STAFF_CMD,
        is_active=True,
    )
    if local is not None:
        staff_qs = staff_qs.filter(Q(local_entrepot=local) | Q(role='admin') | Q(is_superuser=True))

    preview_short = (preview or '').strip()
    if len(preview_short) > 120:
        preview_short = preview_short[:117] + '…'

    for user in staff_qs:
        Notification.objects.create(
            type_notification='chat_client',
            titre='Nouveau chat client',
            message=(
                f'{_client_label(conversation)} : {preview_short}'
                if preview_short
                else f'Nouvelle demande de chat — {_client_label(conversation)}'
            ),
            utilisateur=user,
        )


@transaction.atomic
def client_send_message(request, body: str) -> tuple[ChatConversation, ChatMessage]:
    body = (body or '').strip()
    if not body:
        raise ValueError('Message vide.')
    if len(body) > 4000:
        body = body[:4000]

    conv = get_or_create_open_conversation(request)
    if conv.status not in OPEN_STATUSES:
        raise ValueError('Cette conversation est fermée.')

    if request.session.get(SESSION_DISMISS_KEY):
        request.session.pop(SESSION_DISMISS_KEY, None)

    is_first = not conv.messages.exists()

    client = _client_user(request)
    now = timezone.now()
    msg = ChatMessage.objects.create(
        conversation=conv,
        sender_type=ChatMessage.SENDER_CLIENT,
        sender=client,
        body=body,
    )
    updates = ['last_message_at', 'updated_at']
    conv.last_message_at = now
    if not conv.subject:
        conv.subject = body[:80]
        updates.append('subject')
    if client and not conv.client_id:
        conv.client = client
        updates.append('client')
    conv.save(update_fields=updates)

    if is_first and conv.status == ChatConversation.STATUS_PENDING:
        ChatMessage.objects.create(
            conversation=conv,
            sender_type=ChatMessage.SENDER_SYSTEM,
            body=PENDING_CLIENT_MESSAGE,
        )
        notify_staff_new_chat(conv, body, request=request)

    clear_typing(conv, 'client')
    publish_typing_event(conv.pk, who='client', typing=False)

    return conv, msg


def client_start_new_thread(request) -> dict:
    """
    Réinitialise l'affichage client sans créer de conversation.
    Le prochain message ouvrira un nouveau thread via get_or_create_open_conversation.
    """
    _ensure_session_key(request)
    client = _client_user(request)
    session_key = request.session.session_key or ''
    qs = ChatConversation.objects.all()
    if client:
        conv = qs.filter(client=client).order_by('-last_message_at', '-created_at').first()
    else:
        conv = (
            qs.filter(session_key=session_key, client__isnull=True)
            .order_by('-last_message_at', '-created_at')
            .first()
        )
    if conv and conv.status == ChatConversation.STATUS_CLOSED:
        request.session[SESSION_DISMISS_KEY] = conv.pk
        request.session.modified = True
    return {
        'conversation': None,
        'status': None,
        'messages': [],
        'show_chips': True,
        'can_send': True,
        'can_start_new': False,
        'messages_hidden': False,
    }


@transaction.atomic
def staff_accept(conversation: ChatConversation, staff_user) -> ChatConversation:
    conversation = maybe_auto_close_idle(conversation)
    if conversation.status not in (
        ChatConversation.STATUS_PENDING,
        ChatConversation.STATUS_ACTIVE,
    ):
        raise ValueError('Cette conversation ne peut plus être acceptée.')
    conversation.status = ChatConversation.STATUS_ACTIVE
    conversation.staff = staff_user
    conversation.handler_mode = ChatConversation.HANDLER_STAFF
    conversation.save(update_fields=['status', 'staff', 'handler_mode', 'updated_at'])
    ChatMessage.objects.create(
        conversation=conversation,
        sender_type=ChatMessage.SENDER_SYSTEM,
        sender=staff_user,
        body=ACCEPT_SYSTEM_MESSAGE,
    )
    conversation.last_message_at = timezone.now()
    conversation.save(update_fields=['last_message_at', 'updated_at'])
    return conversation


@transaction.atomic
def staff_refuse(conversation: ChatConversation, staff_user) -> ChatConversation:
    conversation = maybe_auto_close_idle(conversation)
    if conversation.status != ChatConversation.STATUS_PENDING:
        raise ValueError('Seule une demande en attente peut être refusée.')
    conversation.status = ChatConversation.STATUS_REFUSED
    conversation.staff = staff_user
    conversation.save(update_fields=['status', 'staff', 'updated_at'])
    ChatMessage.objects.create(
        conversation=conversation,
        sender_type=ChatMessage.SENDER_SYSTEM,
        sender=staff_user,
        body=REFUSE_CLIENT_MESSAGE,
    )
    conversation.last_message_at = timezone.now()
    conversation.save(update_fields=['last_message_at', 'updated_at'])
    return conversation


@transaction.atomic
def staff_close(conversation: ChatConversation, staff_user) -> ChatConversation:
    conversation = maybe_auto_close_idle(conversation)
    if conversation.status != ChatConversation.STATUS_ACTIVE:
        if conversation.status == ChatConversation.STATUS_CLOSED:
            return conversation
        raise ValueError('Seule une conversation active peut être terminée.')
    return _close_conversation(conversation, staff_user=staff_user)


@transaction.atomic
def staff_send_message(conversation: ChatConversation, staff_user, body: str) -> ChatMessage:
    conversation = maybe_auto_close_idle(conversation)
    body = (body or '').strip()
    if not body:
        raise ValueError('Message vide.')
    if len(body) > 4000:
        body = body[:4000]
    if conversation.status != ChatConversation.STATUS_ACTIVE:
        raise ValueError('La conversation n’est pas active.')
    msg = ChatMessage.objects.create(
        conversation=conversation,
        sender_type=ChatMessage.SENDER_STAFF,
        sender=staff_user,
        body=body,
    )
    conversation.last_message_at = timezone.now()
    if not conversation.staff_id:
        conversation.staff = staff_user
        conversation.save(update_fields=['last_message_at', 'staff', 'updated_at'])
    else:
        conversation.save(update_fields=['last_message_at', 'updated_at'])
    clear_typing(conversation, 'staff')
    publish_typing_event(conversation.pk, who='staff', typing=False)
    return msg


def _conversations_in_period(date_debut: date | None = None, date_fin: date | None = None):
    qs = ChatConversation.objects.select_related('client', 'staff')
    if date_debut and date_fin:
        qs = qs.filter(created_at__date__gte=date_debut, created_at__date__lte=date_fin)
    return qs


def staff_chat_stats(user=None, date_debut: date | None = None, date_fin: date | None = None) -> dict:
    """Statistiques pour l'inbox / historique (optionnellement bornées à une période)."""
    qs = _conversations_in_period(date_debut, date_fin)
    total = qs.count()
    en_cours = qs.filter(status__in=OPEN_STATUSES).count()
    terminees = qs.filter(
        status__in=(
            ChatConversation.STATUS_CLOSED,
            ChatConversation.STATUS_REFUSED,
        )
    ).count()
    msg_qs = ChatMessage.objects.filter(sender_type=ChatMessage.SENDER_CLIENT)
    if date_debut and date_fin:
        msg_qs = msg_qs.filter(
            created_at__date__gte=date_debut,
            created_at__date__lte=date_fin,
        )
    return {
        'total': total,
        'messages_recus': msg_qs.count(),
        'en_cours': en_cours,
        'terminees': terminees,
    }


def staff_list_conversations(user, status: str | None = None):
    """Liste des conversations pour le staff (pending / active par défaut)."""
    cutoff = timezone.now() - timedelta(minutes=IDLE_CLOSE_MINUTES)
    idle_qs = ChatConversation.objects.filter(status__in=OPEN_STATUSES).filter(
        Q(last_message_at__lte=cutoff)
        | Q(last_message_at__isnull=True, created_at__lte=cutoff)
    )
    for conv in idle_qs.iterator():
        maybe_auto_close_idle(conv)

    qs = ChatConversation.objects.select_related('client', 'staff').order_by(
        '-last_message_at', '-created_at'
    )
    if status:
        qs = qs.filter(status=status)
    else:
        qs = qs.filter(
            status__in=(
                ChatConversation.STATUS_PENDING,
                ChatConversation.STATUS_ACTIVE,
            )
        )
    return qs


def staff_list_history_conversations(
    user,
    status: str | None = None,
    date_debut: date | None = None,
    date_fin: date | None = None,
):
    """Toutes les conversations (tous statuts) pour l'historique, filtrées par période."""
    qs = _conversations_in_period(date_debut, date_fin).order_by(
        '-last_message_at', '-created_at'
    )
    if status:
        qs = qs.filter(status=status)
    return qs


def staff_delete_conversation(conversation: ChatConversation) -> None:
    """Suppression définitive d'une conversation et de ses messages."""
    conversation.delete()


def serialize_conversation_list_item(conv: ChatConversation) -> dict:
    last = conv.messages.order_by('-created_at').first()
    return {
        'id': conv.pk,
        'status': conv.status,
        'subject': conv.subject or (last.body[:80] if last else 'Conversation'),
        'preview': (last.body[:120] if last else ''),
        'client_label': _client_label(conv),
        'staff_name': (
            (conv.staff.get_full_name() or conv.staff.username) if conv.staff_id else None
        ),
        'last_message_at': conv.last_message_at.isoformat() if conv.last_message_at else None,
        'closed_at': conv.closed_at.isoformat() if conv.closed_at else None,
        'created_at': conv.created_at.isoformat() if conv.created_at else None,
    }


def conversation_thread_payload(request) -> dict:
    """État thread client (polling)."""
    conv = get_current_conversation(request)
    if not conv:
        return {
            'conversation': None,
            'status': None,
            'messages': [],
            'show_chips': True,
            'can_send': True,
            'can_start_new': False,
            'messages_hidden': False,
        }
    data = serialize_conversation(
        conv,
        include_messages=True,
        for_client=True,
        viewer_role='client',
    )
    show_chips = conv.status == ChatConversation.STATUS_REFUSED
    can_send = conv.status in OPEN_STATUSES
    can_start_new = conv.status == ChatConversation.STATUS_CLOSED
    # Après refus : on peut encore envoyer (nouveau thread) via chips / message
    if conv.status == ChatConversation.STATUS_REFUSED:
        can_send = True
    return {
        'conversation': data,
        'status': conv.status,
        'messages': data.get('messages', []),
        'show_chips': show_chips,
        'can_send': can_send,
        'can_start_new': can_start_new,
        'messages_hidden': data.get('messages_hidden', False),
        'typing': data.get('typing', False),
        'typing_label': data.get('typing_label'),
    }


def client_signal_typing(request) -> dict | None:
    """Pulse « en train d'écrire » côté client."""
    conv = get_current_conversation(request)
    if conv is None or conv.status not in OPEN_STATUSES:
        return None
    client = _client_user(request)
    set_typing(conv, 'client')
    publish_typing_event(
        conv.pk,
        who='client',
        typing=True,
        label='Client écrit…',
    )
    return {'conversation_id': conv.pk}


def staff_signal_typing(conversation: ChatConversation, staff_user) -> None:
    """Pulse « en train d'écrire » côté staff."""
    if conversation.status != ChatConversation.STATUS_ACTIVE:
        return
    set_typing(conversation, 'staff')
    staff_name = staff_user.get_full_name() or staff_user.username or 'Conseiller'
    publish_typing_event(
        conversation.pk,
        who='staff',
        typing=True,
        label=f'{staff_name} écrit…',
    )
