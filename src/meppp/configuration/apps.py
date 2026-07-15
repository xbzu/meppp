from django.apps import AppConfig


class ConfigurationConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "meppp.configuration"
    verbose_name = "站点配置"
