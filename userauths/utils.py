"""
Utils for email sending
"""

import logging
from email.mime.image import MIMEImage
from pathlib import Path

from django.conf import settings
from django.core.mail import EmailMultiAlternatives
from django.template.loader import render_to_string
from django.urls import reverse
from django.utils import timezone
from django.utils.html import strip_tags

logger = logging.getLogger(__name__)

LOGO_CID = 'pb_logo'


def _logo_path():
    return Path(settings.BASE_DIR) / 'static' / 'logo.png'


def get_email_branding_context():
    return _email_brand_context()


def _email_brand_context():
    has_logo = _logo_path().is_file()
    return {
        'site_name': 'AUTO-PIECE',
        'site_tagline': 'Pièces automobiles',
        'has_logo': has_logo,
        'logo_src': f'cid:{LOGO_CID}' if has_logo else '',
        'logo_cid': LOGO_CID,
        'color_primary': '#FF7A20',
        'color_secondary': '#F97316',
        'color_green': '#16a34a',
        'color_text': '#5A6570',
        'color_heading': '#333E50',
        'color_muted': '#7D8590',
        'color_bg': '#F0F2F5',
        'color_card': '#ffffff',
        'color_accent_bg': '#FFF7F0',
    }


def _attach_logo(email):
    logo_path = _logo_path()
    if not logo_path.is_file():
        return
    with logo_path.open('rb') as logo_file:
        logo_img = MIMEImage(logo_file.read())
    logo_img.add_header('Content-ID', f'<{LOGO_CID}>')
    logo_img.add_header('Content-Disposition', 'inline', filename='logo.png')
    email.attach(logo_img)


def _send_branded_email(*, subject, recipient, template_name, context):
    full_context = {**_email_brand_context(), **context}
    html_body = render_to_string(template_name, full_context)
    text_body = strip_tags(html_body)
    from_email = settings.DEFAULT_FROM_EMAIL or settings.EMAIL_HOST_USER

    email = EmailMultiAlternatives(
        subject=subject,
        body=text_body,
        from_email=from_email,
        to=[recipient],
    )
    email.mixed_subtype = 'related'
    email.attach_alternative(html_body, 'text/html')
    _attach_logo(email)
    email.send(fail_silently=False)


def _activation_url(token, request=None):
    path = reverse('activate-account', kwargs={'token': token})
    if request:
        return request.build_absolute_uri(path)
    base = (getattr(settings, 'FRONTEND_URL', None) or 'http://127.0.0.1:8000').rstrip('/')
    return base + path


def send_welcome_activation_email(user, token, request=None, temporary_password=None):
    try:
        _send_branded_email(
            subject='Bienvenue sur AUTO-PIECE — Activez votre compte',
            recipient=user.email,
            template_name='email/welcome_register.html',
            context={
                'user': user,
                'activation_url': _activation_url(token, request),
                'token': token,
                'temporary_password': temporary_password,
            },
        )
        return True
    except Exception as e:
        logger.error("Erreur envoi email activation: %s", e, exc_info=True)
        return False


def send_verification_email(user, token, request=None):
    try:
        _send_branded_email(
            subject='Activez votre compte — AUTO-PIECE',
            recipient=user.email,
            template_name='email/verify_email.html',
            context={
                'user': user,
                'verification_url': _activation_url(token, request),
                'token': token,
            },
        )
        return True
    except Exception as e:
        logger.error("Erreur envoi email vérification: %s", e, exc_info=True)
        return False


def send_password_reset_otp_email(user, otp):
    try:
        _send_branded_email(
            subject='Code de réinitialisation — AUTO-PIECE',
            recipient=user.email,
            template_name='email/pwd_reset_otp.html',
            context={
                'user': user,
                'otp': otp,
                'expires_in': '5 minutes',
            },
        )
        return True
    except Exception as e:
        logger.error("Erreur envoi email OTP: %s", e, exc_info=True)
        return False


def send_account_activation_otp_email(user, otp):
    try:
        _send_branded_email(
            subject='Activez votre compte — AUTO-PIECE',
            recipient=user.email,
            template_name='email/activation_otp.html',
            context={
                'user': user,
                'otp': otp,
                'expires_in': '5 minutes',
            },
        )
        return True
    except Exception as e:
        logger.error("Erreur envoi email OTP activation: %s", e, exc_info=True)
        return False


def send_email_with_html_body(subjet, receivers, template, context):
    if not receivers:
        return False
    try:
        _send_branded_email(
            subject=subjet,
            recipient=receivers[0],
            template_name=template,
            context=context,
        )
        return True
    except Exception as e:
        logger.error("Erreur envoi email: %s", e, exc_info=True)
        return False


def send_account_activation_email(user, request, temporary_password=None):
    from .models import EmailVerificationToken

    EmailVerificationToken.objects.filter(user=user, used=False).update(
        used=True,
        used_at=timezone.now(),
    )
    token = EmailVerificationToken.objects.create(user=user)
    return send_welcome_activation_email(
        user,
        token.token,
        request=request,
        temporary_password=temporary_password,
    )


def send_google_link_otp_email(user, otp):
    """Code de confirmation avant de lier un compte Google a un compte existant.

    Envoye uniquement quand l'adresse Google correspond a un compte deja cree
    avec un mot de passe : il faut alors prouver que la personne controle aussi
    ce compte-la, et pas seulement la boite Google.
    """
    try:
        _send_branded_email(
            subject='Confirmez la liaison de votre compte Google — AUTO-PIECE',
            recipient=user.email,
            template_name='email/google_link_otp.html',
            context={
                'user': user,
                'otp': otp,
                'expires_in': '5 minutes',
            },
        )
        return True
    except Exception as e:
        logger.error("Erreur envoi email OTP liaison Google: %s", e, exc_info=True)
        return False
