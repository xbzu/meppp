from django.urls import path

from . import views

app_name = "health"

urlpatterns = [
    path("live", views.live, name="live"),
    path("ready", views.ready, name="ready"),
]
