from django.conf import settings


def dbm_ui_flags(_request):
    return {
        "DBM_UI_V1_ONLY": bool(getattr(settings, "DBM_UI_V1_ONLY", False)),
        "DBM_UI_USE_API_FOR_MUTATIONS": bool(getattr(settings, "DBM_UI_USE_API_FOR_MUTATIONS", False)),
        "DBM_UI_LEGACY_FALLBACK": bool(getattr(settings, "DBM_UI_LEGACY_FALLBACK", False)),
    }
