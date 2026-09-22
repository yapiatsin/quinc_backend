"""Connexion par compte Google.

Le navigateur obtient un jeton d'identite (JWT) aupres de Google Identity
Services, puis le transmet ici. Le serveur le verifie contre les cles publiques
de Google avant d'ouvrir la moindre session : un jeton n'est jamais cru sur
parole, sinon n'importe qui pourrait en forger un et se faire passer pour un
autre utilisateur.

Trois situations apres verification :

  1. L'identite Google est deja liee a un compte  → connexion immediate.
  2. L'e-mail est inconnu                         → creation d'un compte client.
  3. L'e-mail correspond a un compte existant     → code OTP exige avant de lier
     les deux identites. C'est le seul cas ou le code protege reellement quelque
     chose : sans lui, quiconque creerait une adresse Google homonyme d'un compte
     existant en prendrait le controle. Une fois la liaison faite, les connexions
     suivantes passent par le cas 1 et sont directes.
"""
import logging
import secrets

from django.conf import settings
from django.contrib.auth import get_user_model
from django.utils import timezone

from .models import GoogleIdentity, PWD_FORGET

logger = logging.getLogger(__name__)

User = get_user_model()

# Meme duree que les autres codes de l'application (cf. userauths.views).
OTP_EXPIRY_SECONDS = 300
OTP_MAX_ATTEMPTS = 5

# Emetteurs legitimes d'un jeton d'identite Google.
_ISSUERS = ('accounts.google.com', 'https://accounts.google.com')


class GoogleAuthError(Exception):
    """Erreur presentable a l'utilisateur (message deja en francais)."""


def is_configured() -> bool:
    """La connexion Google n'est proposee que si un identifiant client existe."""
    return bool(getattr(settings, 'GOOGLE_OAUTH_CLIENT_ID', ''))


def verify_credential(credential: str) -> dict:
    """Valide le jeton aupres de Google et retourne ses informations.

    google-auth verifie la signature, l'emetteur, le destinataire (notre
    identifiant client) et la date d'expiration. Un jeton destine a une autre
    application est donc rejete.
    """
    if not credential:
        raise GoogleAuthError("Jeton Google absent.")
    if not is_configured():
        raise GoogleAuthError(
            "La connexion Google n'est pas configurée sur ce serveur."
        )

    try:
        from google.auth.transport import requests as google_requests
        from google.oauth2 import id_token
    except ImportError as exc:  # pragma: no cover - dependance absente
        logger.error("google-auth non installe : %s", exc)
        raise GoogleAuthError(
            "La connexion Google est indisponible sur ce serveur."
        ) from exc

    try:
        info = id_token.verify_oauth2_token(
            credential,
            google_requests.Request(),
            settings.GOOGLE_OAUTH_CLIENT_ID,
        )
    except ValueError as exc:
        logger.warning("Jeton Google refuse : %s", exc)
        raise GoogleAuthError("Jeton Google invalide ou expiré.") from exc

    if info.get('iss') not in _ISSUERS:
        raise GoogleAuthError("Émetteur du jeton Google inattendu.")

    # Une adresse non verifiee par Google ne prouve rien : elle ne doit jamais
    # servir a retrouver un compte existant.
    if not info.get('email_verified'):
        raise GoogleAuthError(
            "Cette adresse Google n'est pas vérifiée. Vérifiez-la puis réessayez."
        )

    email = (info.get('email') or '').strip().lower()
    if not email:
        raise GoogleAuthError("Le compte Google ne fournit pas d'adresse e-mail.")

    return {
        'sub': info['sub'],
        'email': email,
        'given_name': info.get('given_name', ''),
        'family_name': info.get('family_name', ''),
        'name': info.get('name', ''),
        'picture': info.get('picture', ''),
    }


def _unique_username(email: str) -> str:
    """Derive un identifiant libre a partir de l'adresse.

    `username` est le USERNAME_FIELD et porte une contrainte d'unicite : on
    suffixe jusqu'a trouver une place plutot que de laisser echouer l'insertion.
    """
    base = (email.split('@')[0] or 'client')[:40].strip('._-') or 'client'
    candidate = base
    suffix = 1
    while User.objects.filter(username__iexact=candidate).exists():
        suffix += 1
        candidate = f"{base}{suffix}"[:100]
    return candidate


def create_client_from_google(profile: dict) -> User:
    """Cree un compte client a partir du profil Google.

    Le compte n'a pas de mot de passe utilisable : `set_unusable_password`
    empeche toute connexion classique tant que l'utilisateur n'en definit pas un,
    et evite qu'un mot de passe vide ne soit accepte.
    """
    user = User(
        username=_unique_username(profile['email']),
        email=profile['email'],
        first_name=profile.get('given_name', '')[:150],
        last_name=profile.get('family_name', '')[:150],
        role='client',
        is_active=True,
    )
    user.set_unusable_password()
    user.save()
    return user


def link_identity(user: User, profile: dict) -> GoogleIdentity:
    """Enregistre (ou rafraichit) le lien entre le compte et l'identite Google."""
    identity, _ = GoogleIdentity.objects.update_or_create(
        sub=profile['sub'],
        defaults={
            'user': user,
            'email': profile['email'],
            'picture': profile.get('picture', '')[:200],
            'last_login_at': timezone.now(),
        },
    )
    return identity


def find_identity(sub: str):
    return GoogleIdentity.objects.filter(sub=sub).select_related('user').first()


def find_user_by_email(email: str):
    return User.objects.filter(email__iexact=email).first()


def generate_otp(user: User) -> int:
    """Cree un code a 6 chiffres pour la liaison d'un compte existant.

    `secrets` et non `random` : ce dernier s'appuie sur un generateur
    deterministe dont la sortie est predictible a partir de quelques tirages.
    """
    code = 100000 + secrets.randbelow(900000)
    # Les codes precedents encore ouverts sont invalides : un seul code vivant
    # a la fois, sinon un ancien e-mail resterait utilisable.
    PWD_FORGET.objects.filter(
        user_id=user,
        purpose=PWD_FORGET.PURPOSE_GOOGLE_LINK,
        status='0',
    ).update(status='1')
    PWD_FORGET.objects.create(
        user_id=user,
        otp=code,
        status='0',
        purpose=PWD_FORGET.PURPOSE_GOOGLE_LINK,
    )
    return code


def consume_otp(user: User, code: int) -> bool:
    """Valide un code et le marque consomme. Retourne False s'il est invalide."""
    entry = (
        PWD_FORGET.objects
        .filter(
            user_id=user,
            otp=code,
            status='0',
            purpose=PWD_FORGET.PURPOSE_GOOGLE_LINK,
        )
        .order_by('-creat_at')
        .first()
    )
    if entry is None:
        return False
    age = (timezone.now() - entry.creat_at).total_seconds()
    if age > OTP_EXPIRY_SECONDS:
        entry.status = '1'
        entry.save(update_fields=['status'])
        return False
    entry.status = '1'
    entry.save(update_fields=['status'])
    return True


def mask_email(email: str) -> str:
    """a.dupont@gmail.com → a•••••t@gmail.com, pour l'affichage."""
    local, _, domain = email.partition('@')
    if len(local) <= 2:
        masked = local[0] + '•' * max(len(local) - 1, 1)
    else:
        masked = f"{local[0]}{'•' * (len(local) - 2)}{local[-1]}"
    return f"{masked}@{domain}"
