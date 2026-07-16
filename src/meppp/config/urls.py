from django.contrib import admin
from django.urls import include, path

handler404 = "meppp.web.views.not_found"

admin.site.site_header = "MEPPP 管理后台"
admin.site.site_title = "MEPPP"
admin.site.index_title = "站点管理"

urlpatterns = [
    path("", include("meppp.web.urls")),
    path("admin/operations/", include("meppp.operations.urls")),
    path("admin/", admin.site.urls),
    path("health/", include("meppp.health.urls")),
]
