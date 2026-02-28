from django.apps import AppConfig


class DatabaseManagerConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'database_manager'

    def ready(self):
        import logging

        from django.conf import settings

        from .application.flag_guardrails import enforce_dbm_flag_guardrails

        strict = bool(getattr(settings, "DBM_FLAG_VALIDATION_STRICT", False))
        enforce_dbm_flag_guardrails(
            settings,
            strict=strict,
            logger=logging.getLogger(__name__),
        )