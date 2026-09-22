import os
from pathlib import Path
from datetime import timedelta
from decouple import config
# Build paths inside the project like this: BASE_DIR / 'subdir'.
BASE_DIR = Path(__file__).resolve().parent.parent
TEMPLATES_DIR = os.path.join(BASE_DIR, 'templates')
# Quick-start development settings - unsuitable for production
# See https://docs.djangoproject.com/en/6.1/howto/deployment/checklist/

# SECURITY WARNING: keep the secret key used in production secret!
SECRET_KEY = 'django-insecure-p@1+m(!if+d5up_kd@v*@nh3mp(*_ook_js+e+9*ijk90jk5h!'

# SECURITY WARNING: don't run with debug turned on in production!
DEBUG = True

ALLOWED_HOSTS = []

# Application definition

INSTALLED_APPS = [
    # 'daphne',
    'django.contrib.admin',
    'django.contrib.auth',
    'django.contrib.contenttypes',
    'django.contrib.sessions',
    'django.contrib.messages',
    'django.contrib.staticfiles',
    'userauths',
    'stock',
    'ecommerce',
    'simple_history',
    'rest_framework',
    'rest_framework_simplejwt.token_blacklist',
    'corsheaders',
]

MIDDLEWARE = [
    'django.middleware.security.SecurityMiddleware',
    'django.contrib.sessions.middleware.SessionMiddleware',
    'django.middleware.common.CommonMiddleware',
    'django.middleware.csrf.CsrfViewMiddleware',
    'django.contrib.auth.middleware.AuthenticationMiddleware',
    'django.contrib.messages.middleware.MessageMiddleware',
    'django.middleware.clickjacking.XFrameOptionsMiddleware',
]

REST_FRAMEWORK = {
    'DEFAULT_AUTHENTICATION_CLASSES': (
        'rest_framework_simplejwt.authentication.JWTAuthentication',
        'rest_framework.authentication.SessionAuthentication',  # ← ajouter
    ),
    'DEFAULT_PERMISSION_CLASSES': (
        'rest_framework.permissions.IsAuthenticated',
    ),
    'DEFAULT_PAGINATION_CLASS':
        'rest_framework.pagination.PageNumberPagination',
    'PAGE_SIZE': 10,
}

CORS_ALLOW_ALL_ORIGINS = True

SIMPLE_JWT = {
    'ACCESS_TOKEN_LIFETIME': timedelta(minutes=60),
    'REFRESH_TOKEN_LIFETIME': timedelta(days=1),
    'AUTH_HEADER_TYPES': ('Bearer',),
    "ROTATE_REFRESH_TOKENS": True,   # ← optionnel mais recommandé
    "BLACKLIST_AFTER_ROTATION": True, # ← nécessaire pour que blacklist() fonctionne
}

ROOT_URLCONF = 'magasin.urls'

TEMPLATES = [
    {
        'BACKEND': 'django.template.backends.django.DjangoTemplates',
        'DIRS': [TEMPLATES_DIR, 'templates'],
        'APP_DIRS': True,
        'OPTIONS': {
            'context_processors': [
                'django.template.context_processors.request',
                'django.contrib.auth.context_processors.auth',
                'django.contrib.messages.context_processors.messages',
            ],
        },
    },
]

WSGI_APPLICATION = 'magasin.wsgi.application'

# Database
# https://docs.djangoproject.com/en/6.1/ref/settings/#databases

DATABASES = {
    'default': {
        'ENGINE': 'django.db.backends.sqlite3',
        'NAME': BASE_DIR / 'db.sqlite3',
    }
}
# Connexion par compte Google (Google Identity Services).
# L'identifiant client est public : il est expose dans la page de connexion et
# sert au navigateur a demander un jeton. C'est le SERVEUR qui valide ensuite ce
# jeton aupres de Google, aucun secret n'est donc necessaire ici.
# Vide = bouton Google masque, l'application reste utilisable sans.
GOOGLE_OAUTH_CLIENT_ID = config('GOOGLE_OAUTH_CLIENT_ID', default='').strip()
# Password validation
# https://docs.djangoproject.com/en/6.1/ref/settings/#auth-password-validators

AUTH_PASSWORD_VALIDATORS = [
    {
        'NAME': 'django.contrib.auth.password_validation.UserAttributeSimilarityValidator',
    },
    {
        'NAME': 'django.contrib.auth.password_validation.MinimumLengthValidator',
    },
    {
        'NAME': 'django.contrib.auth.password_validation.CommonPasswordValidator',
    },
    {
        'NAME': 'django.contrib.auth.password_validation.NumericPasswordValidator',
    },
]

# Internationalization
# https://docs.djangoproject.com/en/6.1/topics/i18n/

LANGUAGE_CODE = 'fr-fr'
TIME_ZONE = 'Africa/Abidjan'
USE_I18N = True
USE_TZ = True

STOCK_ALERT_TIMEZONE = 'Africa/Abidjan'
STOCK_ALERT_HOURS = (
    (8, 0),   # matin
    (10, 0),  # midi
    (13, 0),  # après-midi
    (15, 0),  # soir
)

# Static files (CSS, JavaScript, Images)
# https://docs.djangoproject.com/en/6.1/howto/static-files/
LOCALE_PATHS = [os.path.join(BASE_DIR, 'locale')]
TIME_ZONE = 'Africa/Abidjan'
USE_I18N = True
USE_L10N = True
USE_TZ = True

# Alertes stock in-app : 4 envois par jour (heure locale Abidjan)
STOCK_ALERT_TIMEZONE = 'Africa/Abidjan'
STOCK_ALERT_HOURS = (
    (8, 0),   # matin
    (10, 0),  # midi
    (13, 0),  # après-midi
    (15, 0),  # soir
)

STATIC_URL = '/static/'
STATICFILES_DIRS = [os.path.join(BASE_DIR, "static")]
STATIC_ROOT = os.path.join(BASE_DIR, "staticfiles")

MEDIA_URL = '/media/'
MEDIA_ROOT = config('MEDIA_ROOT', default=os.path.join(BASE_DIR, 'media'))

# Email
# https://docs.djangoproject.com/en/6.1/topics/email/#topic-email-configuration

STORAGES = {
    'default': {
        'BACKEND': 'django.core.files.storage.FileSystemStorage',
    },
    'staticfiles': {
        'BACKEND': 'whitenoise.storage.CompressedStaticFilesStorage',
    },
}

MAILERS = {
    'default': {
        'BACKEND': 'django.core.mail.backends.console.EmailBackend',
    },
}

# Django impose « same-origin » par defaut depuis la version 4.0, ce qui coupe
# window.opener pour toute fenetre surgissante d'une autre origine. Google
# Identity Services ouvre accounts.google.com dans une telle fenetre et doit
# renvoyer le jeton d'identite a la page appelante par cette reference : sans
# assouplissement, la fenetre reste blanche et la connexion n'aboutit jamais.
# « same-origin-allow-popups » conserve l'isolement de la page vis-a-vis des
# documents qui l'ouvriraient, tout en laissant celles qu'elle ouvre repondre.
SECURE_CROSS_ORIGIN_OPENER_POLICY = config(
    'SECURE_CROSS_ORIGIN_OPENER_POLICY',
    default='same-origin-allow-popups',
).strip()
# Redirection HTTP -> HTTPS assuree par Caddy, pas par Django (evite les boucles).
SECURE_SSL_REDIRECT = False
SECURE_HSTS_SECONDS = config('SECURE_HSTS_SECONDS', default=0, cast=int)
SECURE_HSTS_INCLUDE_SUBDOMAINS = True
SECURE_HSTS_PRELOAD = True

# --- Journalisation : tout sur stdout, recupere par `docker compose logs` ------
LOGGING = {
    'version': 1,
    'disable_existing_loggers': False,
    'formatters': {
        'simple': {'format': '[{asctime}] {levelname} {name} {message}', 'style': '{'},
    },
    'handlers': {
        'console': {'class': 'logging.StreamHandler', 'formatter': 'simple'},
    },
    'root': {'handlers': ['console'], 'level': config('LOG_LEVEL', default='INFO')},
    'loggers': {
        'django.request': {'handlers': ['console'], 'level': 'ERROR', 'propagate': False},
    },
}
# Default primary key field type
# https://docs.djangoproject.com/en/5.0/ref/settings/#default-auto-field

DEFAULT_AUTO_FIELD = 'django.db.models.BigAutoField'
AUTH_USER_MODEL = 'userauths.CustomUser'



# EMAIL_HOST = config('EMAIL_HOST')
# EMAIL_PORT = config('EMAIL_PORT', cast=int)
# EMAIL_USE_TLS = config('EMAIL_USE_TLS', cast=bool)
# EMAIL_HOST_USER = config('EMAIL_HOST_USER')
# # Mot de passe d'application Gmail : retirer les espaces éventuels
# EMAIL_HOST_PASSWORD = config('EMAIL_HOST_PASSWORD').replace(' ', '')

# EMAIL_BACKEND = 'django.core.mail.backends.smtp.EmailBackend'
# EMAIL_TIMEOUT = config('EMAIL_TIMEOUT', default=30, cast=int)

# EMAIL_USE_SSL = config('EMAIL_USE_SSL', default=False, cast=bool)
# DEFAULT_FROM_EMAIL = config('DEFAULT_FROM_EMAIL', default=EMAIL_HOST_USER)
# FRONTEND_URL = config('FRONTEND_URL', default='http://127.0.0.1:8000')
# PUBLIC_BASE_URL = config('PUBLIC_BASE_URL', default='http://127.0.0.1:8000')


SECURE_PROXY_SSL_HEADER = ('HTTP_X_FORWARDED_PROTO', 'https')
USE_X_FORWARDED_HOST = True

# Mettre SECURE_COOKIES=False tant que le site est expose en HTTP simple (IP nue),
# sinon la session et le jeton CSRF ne sont jamais renvoyes par le navigateur.

_secure_cookies = config('SECURE_COOKIES', default=not DEBUG, cast=bool)
SESSION_COOKIE_SECURE = _secure_cookies
CSRF_COOKIE_SECURE = _secure_cookies
SESSION_COOKIE_HTTPONLY = True
X_FRAME_OPTIONS = 'DENY'
SECURE_CONTENT_TYPE_NOSNIFF = True
SECURE_REFERRER_POLICY = 'same-origin'

GENIUSPAY_API_KEY = config('GENIUSPAY_API_KEY', default='').strip()
GENIUSPAY_API_SECRET = config('GENIUSPAY_API_SECRET', default='').strip()
GENIUSPAY_BASE_URL = config(
    'GENIUSPAY_BASE_URL',
    default='https://pay.genius.ci/api/v1/merchant',
).strip().rstrip('/')
GENIUSPAY_WEBHOOK_SECRET = config('GENIUSPAY_WEBHOOK_SECRET', default='').strip()
GENIUSPAY_MIN_AMOUNT = 200

# # Pusher (temps réel magasin — remplace MQTT local)
PUSHER_APP_ID = config('PUSHER_APP_ID', default='')
PUSHER_KEY = config('PUSHER_KEY', default='')
PUSHER_SECRET = config('PUSHER_SECRET', default='')
PUSHER_CLUSTER = config('PUSHER_CLUSTER', default='eu')

# WhatsApp business number (e-com + partage lien paiement caisse) — chiffres seuls, ex. 2250787532210
WHATSAPP_BUSINESS_NUMBER = config(
    'WHATSAPP_BUSINESS_NUMBER',
    default='2250787532210',
).strip().lstrip('+')

# Chatbot e-com — clé IA (non utilisée pour l’instant)
CHATBOT_AI_API_KEY = config('CHATBOT_AI_API_KEY', default='').strip()




