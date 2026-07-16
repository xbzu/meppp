from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.db.migrations.recorder import MigrationRecorder
from django.test import TransactionTestCase


class SecureAttachmentMigrationTests(TransactionTestCase):
    migrate_from = [("publishing", "0004_pendingcomment_pendingentry_contentreviewdecision")]
    migrate_to = [("publishing", "0005_secure_attachment_contract")]

    def _assert_legacy_attachment_is_preserved_and_blocks_upgrade(
        self,
        *,
        file_name,
        mime_type,
        width,
        height,
    ):
        executor = MigrationExecutor(connection)
        executor.migrate(self.migrate_from)
        old_apps = executor.loader.project_state(self.migrate_from).apps
        User = old_apps.get_model("accounts", "User")
        Entry = old_apps.get_model("publishing", "Entry")
        Attachment = old_apps.get_model("publishing", "Attachment")
        author = User.objects.create(username=f"legacy-{mime_type}-{file_name}")
        entry = Entry.objects.create(author=author, body="legacy attachment")
        attachment = Attachment.objects.create(
            entry=entry,
            file=file_name,
            mime_type=mime_type,
            byte_size=128,
            width=width,
            height=height,
        )

        try:
            with self.assertRaisesMessage(
                RuntimeError,
                "requires the legacy attachment table to be empty",
            ):
                MigrationExecutor(connection).migrate(self.migrate_to)

            self.assertNotIn(
                ("publishing", "0005_secure_attachment_contract"),
                MigrationRecorder(connection).applied_migrations(),
            )
            preserved = Attachment.objects.get(pk=attachment.pk)
            self.assertEqual(preserved.file.name, file_name)
            self.assertEqual(preserved.mime_type, mime_type)
        finally:
            Attachment.objects.all().delete()
            MigrationExecutor(connection).migrate(self.migrate_to)

    def test_legacy_jpeg_blocks_upgrade_without_changing_the_row(self):
        self._assert_legacy_attachment_is_preserved_and_blocks_upgrade(
            file_name="entries/legacy.jpg",
            mime_type="image/jpeg",
            width=None,
            height=None,
        )

    def test_unverified_canonical_looking_webp_also_blocks_upgrade(self):
        self._assert_legacy_attachment_is_preserved_and_blocks_upgrade(
            file_name="entries/legacy.webp",
            mime_type="image/webp",
            width=64,
            height=64,
        )
