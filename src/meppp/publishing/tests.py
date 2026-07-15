from django.core.exceptions import ValidationError
from django.test import TestCase

from meppp.accounts.models import User

from .models import ContentState, Entry, Topic


class PublishingModelTests(TestCase):
    def setUp(self):
        self.author = User.objects.create_user(username="author")

    def test_public_queryset_excludes_hidden_and_deleted_entries(self):
        visible = Entry.objects.create(author=self.author, body="visible")
        Entry.objects.create(author=self.author, body="hidden", state=ContentState.HIDDEN)
        Entry.objects.create(author=self.author, body="deleted", state=ContentState.DELETED)

        self.assertEqual(list(Entry.objects.public()), [visible])

    def test_topic_slug_is_normalized(self):
        topic = Topic.objects.create(slug="  Python ", label="Python")

        self.assertEqual(topic.slug, "python")

    def test_entries_use_soft_delete_state(self):
        entry = Entry.objects.create(author=self.author, body="preserve me")

        with self.assertRaises(ValidationError):
            entry.delete()
        with self.assertRaises(ValidationError):
            Entry.objects.filter(pk=entry.pk).delete()
        with self.assertRaises(ValidationError):
            Entry._base_manager.filter(pk=entry.pk).delete()
