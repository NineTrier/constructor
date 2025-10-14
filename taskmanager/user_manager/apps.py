from django.apps import AppConfig


class UserManagerConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'user_manager'

    def ready(self):
        from .roles import ensure_roles_exist

        ensure_roles_exist()
