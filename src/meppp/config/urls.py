from django.contrib import admin
from django.urls import include, path

admin.site.site_header = "MEPPP 管理后台"
admin.site.site_title = "MEPPP"
admin.site.index_title = "站点管理"

urlpatterns = [
    path("admin/", admin.site.urls),
    path("health/", include("meppp.health.urls")),
]
