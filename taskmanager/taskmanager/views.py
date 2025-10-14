from django.shortcuts import render


def permission_denied_view(request, exception=None):
    context = {
        "request_path": request.get_full_path() if request is not None else None,
    }
    return render(request, "403.html", context=context, status=403)
