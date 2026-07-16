from django.db import DatabaseError, connection
from django.http import JsonResponse
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_GET


@never_cache
@require_GET
def live(request):
    return JsonResponse({"status": "ok"})


@never_cache
@require_GET
def ready(request):
    try:
        with connection.cursor() as cursor:
            cursor.execute("SELECT 1")
            database_ready = cursor.fetchone() == (1,)
    except DatabaseError:
        return JsonResponse({"status": "unavailable"}, status=503)
    if not database_ready:
        return JsonResponse({"status": "unavailable"}, status=503)
    return JsonResponse({"status": "ready"})
