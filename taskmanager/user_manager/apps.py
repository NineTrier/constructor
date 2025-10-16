import os
import sys

from django.apps import AppConfig
from django.db import OperationalError, ProgrammingError


class UserManagerConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'user_manager'

    _SKIP_COMMANDS = {
        'makemigrations',
        'migrate',
        'collectstatic',
        'loaddata',
        'flush',
        'shell',
        'test',
    }

    def ready(self):
        # Avoid touching the database for commands that do not require it.
        if len(sys.argv) > 1 and sys.argv[1] in self._SKIP_COMMANDS:
            return

        if os.environ.get('DJANGO_SKIP_ROLE_SYNC', '').lower() in {'1', 'true', 'yes'}:
            return

        from .roles import ensure_roles_exist

        try:
            ensure_roles_exist()
        except (OperationalError, ProgrammingError):
            # Database might not be available yet; skip silently.
            pass
