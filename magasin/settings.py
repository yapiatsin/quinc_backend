import os
from pathlib import Path

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
# MEDIA_ROOT = config('MEDIA_ROOT', default=os.path.join(BASE_DIR, 'media'))

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

SECURE_PROXY_SSL_HEADER = ('HTTP_X_FORWARDED_PROTO', 'https')
USE_X_FORWARDED_HOST = True

# Mettre SECURE_COOKIES=False tant que le site est expose en HTTP simple (IP nue),
# sinon la session et le jeton CSRF ne sont jamais renvoyes par le navigateur.
# _secure_cookies = config('SECURE_COOKIES', default=not DEBUG, cast=bool)
# SESSION_COOKIE_SECURE = _secure_cookies
# CSRF_COOKIE_SECURE = _secure_cookies
SESSION_COOKIE_HTTPONLY = True
X_FRAME_OPTIONS = 'DENY'
SECURE_CONTENT_TYPE_NOSNIFF = True
SECURE_REFERRER_POLICY = 'same-origin'

# GENIUSPAY_API_KEY = config('GENIUSPAY_API_KEY', default='').strip()
# GENIUSPAY_API_SECRET = config('GENIUSPAY_API_SECRET', default='').strip()
# GENIUSPAY_BASE_URL = config(
#     'GENIUSPAY_BASE_URL',
#     default='https://pay.genius.ci/api/v1/merchant',
# ).strip().rstrip('/')
# GENIUSPAY_WEBHOOK_SECRET = config('GENIUSPAY_WEBHOOK_SECRET', default='').strip()
# GENIUSPAY_MIN_AMOUNT = 200

# WhatsApp business (e-com + partage lien paiement caisse) — chiffres seuls, ex. 2250787532210
# WHATSAPP_BUSINESS_NUMBER = config(
#     'WHATSAPP_BUSINESS_NUMBER',
#     default='2250787532210',
# ).strip().lstrip('+')

# Connexion par compte Google (Google Identity Services).
# L'identifiant client est public : il est expose dans la page de connexion et
# sert au navigateur a demander un jeton. C'est le SERVEUR qui valide ensuite ce
# jeton aupres de Google, aucun secret n'est donc necessaire ici.
# Vide = bouton Google masque, l'application reste utilisable sans.
# GOOGLE_OAUTH_CLIENT_ID = config('GOOGLE_OAUTH_CLIENT_ID', default='').strip()

# # Chatbot e-com — clé IA (non utilisée pour l’instant)
# CHATBOT_AI_API_KEY = config('CHATBOT_AI_API_KEY', default='').strip()

# # Pusher (temps réel magasin — remplace MQTT local)
# PUSHER_APP_ID = config('PUSHER_APP_ID', default='')
# PUSHER_KEY = config('PUSHER_KEY', default='')
# PUSHER_SECRET = config('PUSHER_SECRET', default='')
# PUSHER_CLUSTER = config('PUSHER_CLUSTER', default='eu')


