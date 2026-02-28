import json
import logging


logger = logging.getLogger(__name__)


class DbmLegacyEndpointLoggingMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        path = str(getattr(request, "path", "") or "")
        if path.startswith("/database/") and not path.startswith("/database/api/v1/"):
            user = getattr(request, "user", None)
            payload = {
                "path": path,
                "method": getattr(request, "method", ""),
                "query": str(getattr(request, "META", {}).get("QUERY_STRING", "") or ""),
                "authenticated": bool(getattr(user, "is_authenticated", False)),
                "user_id": getattr(user, "id", None),
            }
            logger.warning("legacy_endpoint_hit %s", json.dumps(payload, ensure_ascii=False, default=str))
        return self.get_response(request)
