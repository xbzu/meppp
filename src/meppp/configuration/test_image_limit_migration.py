from django.db import IntegrityError, connection, transaction
from django.db.migrations.executor import MigrationExecutor
from django.test import TransactionTestCase


class ImageLimitMigrationTests(TransactionTestCase):
    migrate_from = [("configuration", "0002_alter_configurationrevision_options")]
    migrate_to = [("configuration", "0003_tighten_image_upload_limit")]

    def test_old_twenty_mebibyte_limit_is_clamped_audited_and_constrained(self):
        executor = MigrationExecutor(connection)
        executor.migrate(self.migrate_from)
        old_apps = executor.loader.project_state(self.migrate_from).apps
        SiteConfiguration = old_apps.get_model("configuration", "SiteConfiguration")
        ConfigurationRevision = old_apps.get_model("configuration", "ConfigurationRevision")
        SiteConfiguration.objects.create(pk=1, upload_max_bytes=20 * 1024 * 1024)
        ConfigurationRevision.objects.create(
            version=1,
            snapshot={"upload_max_bytes": 20 * 1024 * 1024},
        )

        try:
            executor = MigrationExecutor(connection)
            executor.migrate(self.migrate_to)
            new_apps = executor.loader.project_state(self.migrate_to).apps
            SiteConfiguration = new_apps.get_model("configuration", "SiteConfiguration")
            ConfigurationRevision = new_apps.get_model(
                "configuration",
                "ConfigurationRevision",
            )
            configuration = SiteConfiguration.objects.get(pk=1)

            self.assertEqual(configuration.upload_max_bytes, 5 * 1024 * 1024)
            self.assertEqual(configuration.version, 2)
            revision = ConfigurationRevision.objects.get(version=2)
            self.assertEqual(revision.snapshot["upload_max_bytes"], 5 * 1024 * 1024)
            self.assertIn("Security migration", revision.reason)

            with self.assertRaises(IntegrityError), transaction.atomic():
                SiteConfiguration.objects.filter(pk=1).update(upload_max_bytes=20 * 1024 * 1024)
        finally:
            MigrationExecutor(connection).migrate(self.migrate_to)
