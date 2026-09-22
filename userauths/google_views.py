"""Vues de connexion par compte Google.

Le bouton Google Identity Services renvoie un jeton d'identite au navigateur,
qui le poste ici. `google_login_view` repond en JSON : soit une URL de
redirection (connexion faite), soit `otp_required` quand l'adresse correspond a
un compte existant qu'il faut confirmer avant de lier.
"""
from datetime import datetime
import json
import logging

from django.contrib import messages
from django.contrib.auth import login
from django.http import JsonResponse
from django.shortcuts import redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_POST

from . import google_auth
from .google_auth import GoogleAuthError
from .utils import send_google_link_otp_email
from .views import _redirect_after_login

logger = logging.getLogger(__name__)

SESSION_KEY = 'google_link_pending'

# Un seul backend est configure (ModelBackend) mais on le nomme explicitement :
# `login()` ne sait pas deviner lequel utiliser des qu'il y en a plusieurs, et
# ajouter un backend un jour ne doit pas casser cette vue silencieusement.
AUTH_BACKEND = 'django.contrib.auth.backends.ModelBackend'


def _extract_credential(request):
    """Le jeton arrive en JSON (fetch) ou en formulaire selon l'appelant."""
    if request.content_type and 'application/json' in request.content_type:
        try:
            return json.loads(request.body or b'{}').get('credential', '')
        except (ValueError, UnicodeDecodeError):
            return ''
    return request.POST.get('credential', '')


def _login_and_redirect(request, user, profile):
    google_auth.link_identity(user, profile)
    login(request, user, backend=AUTH_BACKEND)
    request.session['show_ecom_order_guide'] = True
    response = _redirect_after_login(user, request)
    civilite = "Mme" if getattr(user, 'genre', '') == "Femme" else "Mr"
    messages.success(request, f"Bienvenue {civilite} {user.username}")
    return JsonResponse({'success': True, 'redirect': response.url})


@require_POST
def google_login_view(request):
    """Point d'entree : verifie le jeton Google et oriente vers le bon cas."""
    try:
        profile = google_auth.verify_credential(_extract_credential(request))
    except GoogleAuthError as exc:
        return JsonResponse({'success': False, 'error': str(exc)}, status=400)

    # Cas 1 : identite deja liee, connexion directe.
    identity = google_auth.find_identity(profile['sub'])
    if identity is not None:
        user = identity.user
        if not user.is_active:
            return JsonResponse(
                {'success': False, 'error': "Votre compte est désactivé."},
                status=403,
            )
        return _login_and_redirect(request, user, profile)

    existing = google_auth.find_user_by_email(profile['email'])

    # Cas 2 : adresse inconnue, on cree un compte client.
    if existing is None:
        user = google_auth.create_client_from_google(profile)
        logger.info("Compte client cree via Google : %s", user.email)
        return _login_and_redirect(request, user, profile)

    # Cas 3 : l'adresse correspond a un compte existant. On exige un code avant
    # de lier les deux identites, sinon un compte Google homonyme suffirait a
    # prendre la main sur un compte cree avec mot de passe.
    if not existing.is_active:
        return JsonResponse(
            {'success': False, 'error': "Votre compte est désactivé."},
            status=403,
        )

    code = google_auth.generate_otp(existing)
    if not send_google_link_otp_email(existing, code):
        return JsonResponse(
            {'success': False,
             'error': "Impossible d'envoyer le code de confirmation. Réessayez."},
            status=502,
        )

    request.session[SESSION_KEY] = {
        'user_pk': existing.pk,
        'profile': profile,
        'started_at': timezone.now().isoformat(),
        'attempts': 0,
    }
    request.session.modified = True
    return JsonResponse({
        'success': True,
        'otp_required': True,
        'redirect': reverse('google_otp'),
    })


def _pending(request):
    data = request.session.get(SESSION_KEY)
    if not data:
        return None
    started = datetime.fromisoformat(data['started_at'])
    if (timezone.now() - started).total_seconds() > google_auth.OTP_EXPIRY_SECONDS:
        request.session.pop(SESSION_KEY, None)
        return None
    return data


def _remaining_seconds(data):
    started = datetime.fromisoformat(data['started_at'])
    elapsed = (timezone.now() - started).total_seconds()
    return max(0, int(google_auth.OTP_EXPIRY_SECONDS - elapsed))


# Renvoi de code : delai minimal entre deux envois, et plafond par demande.
# Sans ces deux garde-fous, le bouton devient un moyen d'inonder une boite mail.
RESEND_COOLDOWN_SECONDS = 60
RESEND_MAX = 3


@require_POST
def google_otp_resend_view(request):
    """Renvoie un nouveau code pour la liaison en cours."""
    data = _pending(request)
    if data is None:
        messages.error(request, "La demande a expiré. Reprenez la connexion avec Google.")
        return redirect('connexion')

    user = google_auth.find_user_by_email(data['profile']['email'])
    if user is None or user.pk != data['user_pk']:
        request.session.pop(SESSION_KEY, None)
        messages.error(request, "Compte introuvable.")
        return redirect('connexion')

    envois = data.get('resends', 0)
    if envois >= RESEND_MAX:
        messages.error(
            request,
            "Nombre de renvois atteint. Reprenez la connexion avec Google.",
        )
        return redirect('google_otp')

    dernier = data.get('last_sent_at') or data['started_at']
    attente = RESEND_COOLDOWN_SECONDS - (timezone.now() - datetime.fromisoformat(dernier)).total_seconds()
    if attente > 0:
        messages.warning(
            request,
            f"Patientez encore {int(attente)} seconde(s) avant de redemander un code.",
        )
        return redirect('google_otp')

    code = google_auth.generate_otp(user)
    if not send_google_link_otp_email(user, code):
        messages.error(request, "L'envoi a échoué. Réessayez dans un instant.")
        return redirect('google_otp')

    # Le nouveau code repart pour une duree pleine. Les tentatives ne sont pas
    # remises a zero : sinon il suffirait de redemander un code pour relancer
    # indefiniment les essais de force brute.
    maintenant = timezone.now().isoformat()
    data['started_at'] = maintenant
    data['last_sent_at'] = maintenant
    data['resends'] = envois + 1
    request.session[SESSION_KEY] = data
    request.session.modified = True

    restants = RESEND_MAX - data['resends']
    messages.success(
        request,
        f"Nouveau code envoyé à {google_auth.mask_email(user.email)}."
        + (f" Il vous reste {restants} renvoi(s)." if restants else ""),
    )
    return redirect('google_otp')


def google_otp_view(request):
    """Saisie du code confirmant la liaison avec un compte existant."""
    data = _pending(request)
    if data is None:
        messages.error(
            request,
            "La demande a expiré. Reprenez la connexion avec Google.",
        )
        return redirect('connexion')

    user = google_auth.find_user_by_email(data['profile']['email'])
    if user is None or user.pk != data['user_pk']:
        request.session.pop(SESSION_KEY, None)
        messages.error(request, "Compte introuvable.")
        return redirect('connexion')

    contexte = {
        'email': google_auth.mask_email(user.email),
        'remaining_seconds': _remaining_seconds(data),
        'otp_expired': _remaining_seconds(data) <= 0,
    }

    if request.method != 'POST':
        return render(request, 'page/google_otp.html', contexte)

    try:
        code = int((request.POST.get('otp') or '').strip())
    except (TypeError, ValueError):
        messages.error(request, "Code invalide.")
        return render(request, 'page/google_otp.html', contexte)

    # Sans plafond, un code a six chiffres se devine par force brute.
    data['attempts'] = data.get('attempts', 0) + 1
    request.session[SESSION_KEY] = data
    request.session.modified = True
    if data['attempts'] > google_auth.OTP_MAX_ATTEMPTS:
        request.session.pop(SESSION_KEY, None)
        messages.error(
            request,
            "Trop de tentatives. Reprenez la connexion avec Google.",
        )
        return redirect('connexion')

    if not google_auth.consume_otp(user, code):
        restantes = google_auth.OTP_MAX_ATTEMPTS - data['attempts'] + 1
        messages.error(
            request,
            f"Code incorrect ou expiré. Il vous reste {max(restantes, 0)} tentative(s).",
        )
        return render(request, 'page/google_otp.html', contexte)

    profile = data['profile']
    request.session.pop(SESSION_KEY, None)
    google_auth.link_identity(user, profile)
    login(request, user, backend=AUTH_BACKEND)
    request.session['show_ecom_order_guide'] = True
    civilite = "Mme" if getattr(user, 'genre', '') == "Femme" else "Mr"
    messages.success(
        request,
        f"Compte Google lié. Bienvenue {civilite} {user.username}",
    )
    return _redirect_after_login(user, request)
