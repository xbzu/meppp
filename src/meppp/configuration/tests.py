from django.contrib.admin.sites import AdminSite
from django.core.exceptions import ValidationError
from django.test import RequestFactory, TestCase
from django.urls import reverse

from meppp.accounts.models import User
from meppp.audit.models import AuditEvent

from .admin import SiteConfigurationAdmin
from .models import ConfigurationRevision, SiteConfiguration
from .services import update_configuration


class ConfigurationServiceTests(TestCase):
    def setUp(self):
        self.actor = User.objects.create_user(username="owner")

    def test_update_is_versioned_and_audited(self):
        configuration = update_configuration(
            actor=self.actor,
            changes={"site_name": "My Community", "post_max_length": 1_500},
            reason="initial policy",
        )

        self.assertEqual(configuration.pk, 1)
        self.assertEqual(configuration.version, 2)
        self.assertEqual(
            list(
                ConfigurationRevision.objects.values_list("version", flat=True).order_by("version")
            ),
            [1, 2],
        )
        self.assertEqual(
            ConfigurationRevision.objects.get(version=2).snapshot["site_name"], "My Community"
        )
        self.assertEqual(AuditEvent.objects.get().action, "configuration.updated")

    def test_feature_switch_updates_are_versioned_in_the_configuration_snapshot(self):
        configuration = update_configuration(
            actor=self.actor,
            changes={
                "video_uploads_enabled": False,
                "x_references_enabled": False,
                "youtube_references_enabled": False,
            },
            reason="pause new media intake",
        )

        revision = ConfigurationRevision.objects.get(version=2)
        self.assertFalse(configuration.video_uploads_enabled)
        self.assertFalse(configuration.x_references_enabled)
        self.assertFalse(configuration.youtube_references_enabled)
        self.assertFalse(revision.snapshot["video_uploads_enabled"])
        self.assertFalse(revision.snapshot["x_references_enabled"])
        self.assertFalse(revision.snapshot["youtube_references_enabled"])
        self.assertEqual(revision.reason, "pause new media intake")

    def test_revision_history_rejects_mutation(self):
        update_configuration(actor=self.actor, changes={"site_name": "My Community"})
        revision = ConfigurationRevision.objects.get(version=1)

        revision.reason = "changed"
        with self.assertRaises(ValidationError):
            revision.save()
        with self.assertRaises(ValidationError):
            ConfigurationRevision.objects.filter(pk=revision.pk).delete()
        with self.assertRaises(ValidationError):
            ConfigurationRevision._base_manager.filter(pk=revision.pk).update(reason="changed")

    def test_noop_update_does_not_create_another_version(self):
        first = update_configuration(actor=self.actor, changes={})
        second = update_configuration(actor=self.actor, changes={})

        self.assertEqual(first.version, 1)
        self.assertEqual(second.version, 1)
        self.assertEqual(ConfigurationRevision.objects.count(), 1)
        self.assertEqual(AuditEvent.objects.count(), 0)

    def test_staff_without_add_permission_cannot_create_configuration_in_admin(self):
        model_admin = SiteConfigurationAdmin(SiteConfiguration, AdminSite())
        request = RequestFactory().get("/admin/configuration/siteconfiguration/add/")
        request.user = User.objects.create_user(username="staff", is_staff=True)

        self.assertFalse(model_admin.has_add_permission(request))

    def test_superuser_can_open_localized_initial_configuration_form(self):
        self.actor.is_staff = True
        self.actor.is_superuser = True
        self.actor.save()
        self.client.force_login(self.actor)

        response = self.client.get(reverse("admin:configuration_siteconfiguration_add"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "站点名称")
        self.assertContains(response, "保存后生成")

    def test_unknown_setting_is_rejected(self):
        with self.assertRaises(ValidationError):
            update_configuration(actor=self.actor, changes={"secret_key": "unsafe"})

    def test_invalid_value_rolls_back_the_transaction(self):
        with self.assertRaises(ValidationError):
            update_configuration(actor=self.actor, changes={"post_max_length": 99})

        self.assertEqual(SiteConfiguration.objects.count(), 0)
        self.assertEqual(ConfigurationRevision.objects.count(), 0)
        self.assertEqual(AuditEvent.objects.count(), 0)
