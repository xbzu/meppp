from django.db import IntegrityError, transaction
from django.test import TestCase

from meppp.accounts.models import User
from meppp.publishing.models import Entry

from .models import EntryLike, Follow


class SocialConstraintTests(TestCase):
    def setUp(self):
        self.first = User.objects.create_user(username="first")
        self.second = User.objects.create_user(username="second")

    def test_user_cannot_follow_self(self):
        with self.assertRaises(IntegrityError), transaction.atomic():
            Follow.objects.create(follower=self.first, followed=self.first)

    def test_follow_is_unique(self):
        Follow.objects.create(follower=self.first, followed=self.second)

        with self.assertRaises(IntegrityError), transaction.atomic():
            Follow.objects.create(follower=self.first, followed=self.second)

    def test_entry_like_is_unique(self):
        entry = Entry.objects.create(author=self.second, body="hello")
        EntryLike.objects.create(actor=self.first, entry=entry)

        with self.assertRaises(IntegrityError), transaction.atomic():
            EntryLike.objects.create(actor=self.first, entry=entry)
