from django.db import DatabaseError

from .models import Profile


def current_profile(request):
    if not request.user.is_authenticated:
        return {}
    try:
        profile = Profile.for_user(request.user)
    except DatabaseError:
        return {}
    if profile is None:
        return {}
    return {"profile": profile}
