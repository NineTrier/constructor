from django.conf import settings


def dbm_ui_flags(_request):
    return {
        "DBM_UI_V1_ONLY": bool(getattr(settings, "DBM_UI_V1_ONLY", False)),
        "DBM_UI_USE_API_FOR_MUTATIONS": bool(getattr(settings, "DBM_UI_USE_API_FOR_MUTATIONS", False)),
        "DBM_UI_LEGACY_FALLBACK": bool(getattr(settings, "DBM_UI_LEGACY_FALLBACK", False)),
        "DBM_LINKS_META_UI": bool(getattr(settings, "DBM_LINKS_META_UI", True)),
        "DOCUMENT_LINK_TREE_UI": bool(getattr(settings, "DOCUMENT_LINK_TREE_UI", True)),
        "DOCUMENT_VARIABLES_TREE_UNIFIED_UI": bool(getattr(settings, "DOCUMENT_VARIABLES_TREE_UNIFIED_UI", True)),
        "DOCUMENT_LINK_TREE_DEBUG": bool(getattr(settings, "DOCUMENT_LINK_TREE_DEBUG", False)),
        "DOCUMENT_EVENT_DRIVEN_UI": bool(getattr(settings, "DOCUMENT_EVENT_DRIVEN_UI", False)),
        "DBM_OBJECT_FORMS_DEBUG": bool(getattr(settings, "DBM_OBJECT_FORMS_DEBUG", False)),
        "DBM_DISABLE_LEGACY_LINKED_PARAMS": bool(getattr(settings, "DBM_DISABLE_LEGACY_LINKED_PARAMS", False)),
        "DOC_TOKEN_HUMAN_STRICT": bool(getattr(settings, "DOC_TOKEN_HUMAN_STRICT", False)),
    }
