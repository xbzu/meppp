import os
import shutil

from django.conf import settings
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
    try:
        media_stats = os.statvfs(settings.MEDIA_ROOT)
        media_ready = (
            settings.MEDIA_ROOT.is_dir()
            and os.access(settings.MEDIA_ROOT, os.R_OK | os.W_OK | os.X_OK)
            and not bool(media_stats.f_flag & getattr(os, "ST_RDONLY", 1))
            and shutil.disk_usage(settings.MEDIA_ROOT).free >= settings.MEDIA_MIN_FREE_BYTES
        )
    except OSError:
        media_ready = False
    if not database_ready or not media_ready:
        return JsonResponse({"status": "unavailable"}, status=503)
    return JsonResponse({"status": "ready"})
