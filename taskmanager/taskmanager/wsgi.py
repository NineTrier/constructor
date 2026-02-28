"""
WSGI config for taskmanager project.

It exposes the WSGI callable as a module-level variable named ``application``.

For more information on this file, see
https://docs.djangoproject.com/en/4.1/howto/deployment/wsgi/
"""

import os

from django.core.wsgi import get_wsgi_application

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'taskmanager.settings')
if os.getenv("DJANGO_FORCE_SECURE_SCHEME", "").lower() in ("1", "true", "yes"):
    os.environ['HTTPS'] = "on"
application = get_wsgi_application()
