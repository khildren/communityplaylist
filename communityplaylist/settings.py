"""
Django settings for communityplaylist project.
"""
import os
from pathlib import Path
from decouple import config, Csv

BASE_DIR = Path(__file__).resolve().parent.parent

SECRET_KEY = config('SECRET_KEY', default='django-insecure-h+uj_=cyo*v!34-=wvv8_xrdk0a-pjpselopb8)aeb-jx+66=c')
DEBUG      = config('DEBUG', default=False, cast=bool)

ALLOWED_HOSTS      = config('ALLOWED_HOSTS', default='communityplaylist.com,www.communityplaylist.com', cast=Csv())
CSRF_TRUSTED_ORIGINS = config('CSRF_TRUSTED_ORIGINS', default='https://communityplaylist.com,https://www.communityplaylist.com', cast=Csv())

# Application definition

INSTALLED_APPS = [
    'django.contrib.admin',
    'django.contrib.auth',
    'django.contrib.contenttypes',
    'django.contrib.sessions',
    'django.contrib.messages',
    'django.contrib.staticfiles',
    'django.contrib.sitemaps',
    'events',
    'board',
]

MIDDLEWARE = [
    'django.middleware.security.SecurityMiddleware',
    'whitenoise.middleware.WhiteNoiseMiddleware',
    'django.contrib.sessions.middleware.SessionMiddleware',
    'django.middleware.common.CommonMiddleware',
    'django.middleware.csrf.CsrfViewMiddleware',
    'django.contrib.auth.middleware.AuthenticationMiddleware',
    'django.contrib.messages.middleware.MessageMiddleware',
    'django.middleware.clickjacking.XFrameOptionsMiddleware',
]

ROOT_URLCONF = 'communityplaylist.urls'

TEMPLATES = [
    {
        'BACKEND': 'django.template.backends.django.DjangoTemplates',
        'DIRS': [BASE_DIR / 'templates'],
        'APP_DIRS': True,
        'OPTIONS': {
            'context_processors': [
                'django.template.context_processors.request',
                'django.contrib.auth.context_processors.auth',
                'django.contrib.messages.context_processors.messages',
                'events.context_processors.admin_pending',
                'events.context_processors.featured_record',
            ],
        },
    },
]

WSGI_APPLICATION = 'communityplaylist.wsgi.application'


# Database
# https://docs.djangoproject.com/en/6.0/ref/settings/#databases

DATABASES = {
    'default': {
        'ENGINE': 'django.db.backends.sqlite3',
        'NAME': BASE_DIR / 'db.sqlite3',
        'OPTIONS': {
            'timeout': 30,  # Wait up to 30s for locked DB (prevents transient 500s during drive sync)
        },
    }
}


# Password validation
# https://docs.djangoproject.com/en/6.0/ref/settings/#auth-password-validators

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
# https://docs.djangoproject.com/en/6.0/topics/i18n/

LANGUAGE_CODE = 'en-us'

TIME_ZONE = 'America/Los_Angeles'

USE_I18N = True

USE_TZ = True


# Static files (CSS, JavaScript, Images)
# https://docs.djangoproject.com/en/6.0/howto/static-files/

STATIC_URL = '/static/'
STATIC_ROOT = BASE_DIR / "staticfiles"
STATICFILES_DIRS = [BASE_DIR / "static"]
MEDIA_URL = '/media/'
MEDIA_ROOT = os.path.join(BASE_DIR, 'media')

# Optional: Eventbrite API key for import_venue_feeds Eventbrite source type
# Get one free at https://www.eventbrite.com/platform/api
EVENTBRITE_API_KEY = config('EVENTBRITE_API_KEY', default='')

# MusicBrainz — no key needed, free & open. Contact email for User-Agent header.
# https://musicbrainz.org/doc/MusicBrainz_API — 1 req/sec rate limit
MUSICBRAINZ_CONTACT = config('MUSICBRAINZ_CONTACT', default='hello@communityplaylist.com')

# Google Drive API key — used to list files in public Drive folders for the music player.
# Get one at https://console.cloud.google.com/ → APIs & Services → Credentials → API Key
# Enable: Google Drive API. Restrict key to: Drive API only.
# Leave blank to disable Drive sync (profiles still work, just no music player content).
GOOGLE_DRIVE_API_KEY = config('GOOGLE_DRIVE_API_KEY', default='')

# YouTube Data API v3 — used to harvest videos from connected artist/venue/promoter channels.
# Same Google Cloud project as Drive: console.cloud.google.com → APIs & Services → Credentials
# Enable "YouTube Data API v3" then create or reuse an API key (restrict to YouTube Data API).
YOUTUBE_API_KEY = config('YOUTUBE_API_KEY', default='')

# Twitch API — used to harvest VODs and detect live streams from connected channels.
# Get credentials at: dev.twitch.tv → Your Console → Register Your Application
# Set OAuth Redirect URL to https://communityplaylist.com, Category: Website Integration
TWITCH_CLIENT_ID     = config('TWITCH_CLIENT_ID', default='')
TWITCH_CLIENT_SECRET = config('TWITCH_CLIENT_SECRET', default='')

# Spotify Web API — artist search, genre tags, images, popularity
SPOTIFY_CLIENT_ID     = config('SPOTIFY_CLIENT_ID', default='')
SPOTIFY_CLIENT_SECRET = config('SPOTIFY_CLIENT_SECRET', default='')

# Bluesky — direct AT Protocol posting on event approval
BLUESKY_HANDLE       = config('BLUESKY_HANDLE', default='')
BLUESKY_APP_PASSWORD = config('BLUESKY_APP_PASSWORD', default='')

# Discord webhooks — configure in .env
DISCORD_WEBHOOK_BOARD  = config('DISCORD_WEBHOOK_BOARD',  default='')  # board topics + Free & Trade
DISCORD_WEBHOOK_EVENTS = config('DISCORD_WEBHOOK_EVENTS', default='')  # new approved events (text/forum)

# Discord bot — needed for native Scheduled Events tab
# Create bot at discord.com/developers, add to server with MANAGE_EVENTS permission
DISCORD_BOT_TOKEN = config('DISCORD_BOT_TOKEN', default='')  # Bot token (not webhook)
DISCORD_GUILD_ID  = config('DISCORD_GUILD_ID',  default='')  # Server/Guild ID (right-click server → Copy ID)

# Social auto-posting limits
SOCIAL_DAILY_POST_LIMIT  = 27  # max Bluesky posts/day; above this, events split by category
SOCIAL_BOARD_DELAY_HOURS = 1   # hours after topic creation before auto-posting

# Last.fm API — user top tracks / recent plays on public profiles
# Register at: https://www.last.fm/api/account/create
LASTFM_API_KEY    = config('LASTFM_API_KEY',    default='f20acc46f0492bbea83a73865f36a735')
LASTFM_API_SECRET = config('LASTFM_API_SECRET', default='31118728f864c32cab4d8af55e5d14c5')

# Discogs API — user collection on public profiles (read-only, no OAuth needed)
# Register at: https://www.discogs.com/settings/developers
DISCOGS_CONSUMER_KEY    = config('DISCOGS_CONSUMER_KEY',    default='NIIjwGGZHxtpGqVViANR')
DISCOGS_CONSUMER_SECRET = config('DISCOGS_CONSUMER_SECRET', default='LuqJsKXktiCLKJYoynYSoikWuWKNyIWb')

# Email — Plesk local SMTP with SASL auth
# Uses communityplaylist/email_backend.py to skip self-signed cert verification
EMAIL_BACKEND   = config('EMAIL_BACKEND', default='communityplaylist.email_backend.LocalSMTPBackend')
EMAIL_HOST      = config('EMAIL_HOST', default='localhost')
EMAIL_PORT      = config('EMAIL_PORT', default=25, cast=int)
EMAIL_USE_TLS   = config('EMAIL_USE_TLS', default=True, cast=bool)
EMAIL_HOST_USER = config('EMAIL_HOST_USER', default='')
EMAIL_HOST_PASSWORD = config('EMAIL_HOST_PASSWORD', default='')
DEFAULT_FROM_EMAIL  = config('DEFAULT_FROM_EMAIL', default='Community Playlist <noreply@communityplaylist.com>')
EMAIL_TIMEOUT = 30
SITE_URL = config('SITE_URL', default='https://communityplaylist.com')

# Admin alert emails (500 errors, etc.)
ADMINS = [('Binsky', 'andrew.jubinsky@proton.me')]
SERVER_EMAIL = 'noreply@communityplaylist.com'
