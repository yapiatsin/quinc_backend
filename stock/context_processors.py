from django.conf import settings


def pusher_config(request):
    """Expose la clé publique Pusher et le WhatsApp business aux templates."""
    wa = (getattr(settings, 'WHATSAPP_BUSINESS_NUMBER', '') or '').strip().lstrip('+')
    return {
        'PUSHER_KEY': getattr(settings, 'PUSHER_KEY', '') or '',
        'PUSHER_CLUSTER': getattr(settings, 'PUSHER_CLUSTER', 'eu') or 'eu',
        'WHATSAPP_BUSINESS_NUMBER': wa,
        'WHATSAPP_BUSINESS_URL': f'https://wa.me/{wa}' if wa else '',
    }
