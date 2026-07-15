from django.db import DatabaseError, connection
from django.http import JsonResponse
from django.views.decorators.http import require_GET

from meppp import __version__


@require_GET
def live(request):
    return JsonResponse({"status": "ok", "service": "meppp", "version": __version__})


@require_GET
def ready(request):
    try:
        with connection.cursor() as cursor:
            cursor.execute("SELECT 1")
            cursor.fetchone()
    except DatabaseError:
        return JsonResponse({"status": "unavailable"}, status=503)
    return JsonResponse({"status": "ready"})
