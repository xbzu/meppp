from django.core.exceptions import ValidationError
from django.test import TestCase

from meppp.accounts.models import User

from .models import AuditEvent
from .services import record_event


class AuditEventTests(TestCase):
    def setUp(self):
        self.actor = User.objects.create_user(username="moderator")

    def test_record_event_creates_append_only_event(self):
        event = record_event(
            actor=self.actor,
            action="content.hidden",
            target_type="entry",
            reason="policy",
        )

        event.reason = "changed"
        with self.assertRaises(ValidationError):
            event.save()
        with self.assertRaises(ValidationError):
            event.delete()

    def test_actor_must_be_user_or_none(self):
        with self.assertRaises(TypeError):
            record_event(action="test", target_type="system", actor=object())

    def test_queryset_mutation_is_rejected(self):
        event = AuditEvent.objects.create(action="test", target_type="system")

        with self.assertRaises(ValidationError):
            AuditEvent.objects.filter(pk=event.pk).update(reason="changed")
        with self.assertRaises(ValidationError):
            AuditEvent.objects.filter(pk=event.pk).delete()
        with self.assertRaises(ValidationError):
            AuditEvent.objects.bulk_update([event], ["reason"])
        with self.assertRaises(ValidationError):
            AuditEvent._base_manager.filter(pk=event.pk).update(reason="changed")

    def test_clone_with_existing_primary_key_cannot_overwrite_event(self):
        event = AuditEvent.objects.create(action="original", target_type="system")
        clone = AuditEvent(
            pk=event.pk,
            public_id=event.public_id,
            created_at=event.created_at,
            action="changed",
            target_type="system",
        )

        with self.assertRaises(ValidationError):
            clone.save()
        event.refresh_from_db()
        self.assertEqual(event.action, "original")
