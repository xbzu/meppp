from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.test import TransactionTestCase


class FeatureSwitchMigrationTests(TransactionTestCase):
    migrate_from = [("configuration", "0003_tighten_image_upload_limit")]
    migrate_to = [("configuration", "0004_site_feature_switches")]

    def test_existing_configuration_defaults_to_enabled_without_rewriting_history(self):
        executor = MigrationExecutor(connection)
        executor.migrate(self.migrate_from)
        old_apps = executor.loader.project_state(self.migrate_from).apps
        SiteConfiguration = old_apps.get_model("configuration", "SiteConfiguration")
        ConfigurationRevision = old_apps.get_model("configuration", "ConfigurationRevision")
        old_snapshot = {
            "site_name": "Existing MEPPP",
            "comments_enabled": False,
        }
        SiteConfiguration.objects.create(pk=1, version=7, site_name="Existing MEPPP")
        ConfigurationRevision.objects.create(version=7, snapshot=old_snapshot)

        try:
            executor = MigrationExecutor(connection)
            executor.migrate(self.migrate_to)
            new_apps = executor.loader.project_state(self.migrate_to).apps
            SiteConfiguration = new_apps.get_model("configuration", "SiteConfiguration")
            ConfigurationRevision = new_apps.get_model("configuration", "ConfigurationRevision")
            configuration = SiteConfiguration.objects.get(pk=1)

            self.assertTrue(configuration.video_uploads_enabled)
            self.assertTrue(configuration.x_references_enabled)
            self.assertTrue(configuration.youtube_references_enabled)
            self.assertEqual(configuration.version, 7)
            self.assertEqual(ConfigurationRevision.objects.count(), 1)
            self.assertEqual(ConfigurationRevision.objects.get(version=7).snapshot, old_snapshot)
        finally:
            MigrationExecutor(connection).migrate(self.migrate_to)
