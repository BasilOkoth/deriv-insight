from pathlib import Path
import os
import dj_database_url

BASE_DIR = Path(__file__).resolve().parent.parent
SECRET_KEY = os.getenv('DJANGO_SECRET_KEY','dev-only-change-me')
DEBUG = os.getenv('DEBUG','false').lower() == 'true'
ALLOWED_HOSTS = [x.strip() for x in os.getenv('ALLOWED_HOSTS','localhost,127.0.0.1,.onrender.com').split(',') if x.strip()]
CSRF_TRUSTED_ORIGINS = [x.strip() for x in os.getenv('CSRF_TRUSTED_ORIGINS','').split(',') if x.strip()]

INSTALLED_APPS = [
    'django.contrib.admin','django.contrib.auth','django.contrib.contenttypes','django.contrib.sessions',
    'django.contrib.messages','django.contrib.staticfiles','research',
]
MIDDLEWARE = [
    'django.middleware.security.SecurityMiddleware','whitenoise.middleware.WhiteNoiseMiddleware',
    'django.contrib.sessions.middleware.SessionMiddleware','django.middleware.common.CommonMiddleware',
    'django.middleware.csrf.CsrfViewMiddleware','django.contrib.auth.middleware.AuthenticationMiddleware',
    'django.contrib.messages.middleware.MessageMiddleware','django.middleware.clickjacking.XFrameOptionsMiddleware',
]
ROOT_URLCONF='deriv_insight.urls'
TEMPLATES=[{'BACKEND':'django.template.backends.django.DjangoTemplates','DIRS':[BASE_DIR/'research'/'templates'],'APP_DIRS':True,'OPTIONS':{'context_processors':['django.template.context_processors.request','django.contrib.auth.context_processors.auth','django.contrib.messages.context_processors.messages']}}]
WSGI_APPLICATION='deriv_insight.wsgi.application'
DATABASES={'default': dj_database_url.config(default=f"sqlite:///{BASE_DIR/'db.sqlite3'}", conn_max_age=600)}
AUTH_PASSWORD_VALIDATORS=[]
LANGUAGE_CODE='en-us'; TIME_ZONE='Africa/Nairobi'; USE_I18N=True; USE_TZ=True
STATIC_URL='static/'; STATIC_ROOT=BASE_DIR/'staticfiles'; STATICFILES_STORAGE='whitenoise.storage.CompressedManifestStaticFilesStorage'
DEFAULT_AUTO_FIELD='django.db.models.BigAutoField'
LOGIN_URL='login'; LOGIN_REDIRECT_URL='overview'; LOGOUT_REDIRECT_URL='login'
SECURE_PROXY_SSL_HEADER=('HTTP_X_FORWARDED_PROTO','https')

DERIV_PUBLIC_WS=os.getenv('DERIV_PUBLIC_WS','wss://api.derivws.com/trading/v1/options/ws/public')
DERIV_REST_BASE=os.getenv('DERIV_REST_BASE','https://api.derivws.com')
DERIV_AUTH_TOKEN=os.getenv('DERIV_AUTH_TOKEN','')
DERIV_APP_ID=os.getenv('DERIV_APP_ID','')
DERIV_ACCOUNT_ID=os.getenv('DERIV_ACCOUNT_ID','')
DERIV_DEMO_ENABLED=os.getenv('DERIV_DEMO_ENABLED','false').lower()=='true'
DERIV_REAL_ENABLED=False  # deliberate hard lock in v1
