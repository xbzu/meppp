from __future__ import annotations

from .models import SiteConfiguration


def get_site_configuration() -> SiteConfiguration:
    return SiteConfiguration.objects.filter(pk=1).first() or SiteConfiguration()
