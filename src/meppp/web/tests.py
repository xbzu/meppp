from __future__ import annotations

from datetime import timedelta
from ipaddress import ip_network
from unittest.mock import patch

from django.core.cache import cache, caches
from django.test import Client, RequestFactory, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from meppp.accounts.models import Profile, User
from meppp.accounts.services import issue_invitation
from meppp.configuration.models import (
    ModerationMode,
    RegistrationMode,
    SiteConfiguration,
)
from meppp.moderation.models import Report, ReportReason
from meppp.notifications.models import Notification, NotificationKind
from meppp.publishing.models import Comment, ContentState, Entry, Topic
from meppp.social.models import EntryLike, Follow

from .models import SubmissionClaim
from .rate_limit import (
    RATE_LIMITS,
    RateLimit,
    RateLimitExceeded,
    client_ip,
    enforce_rate_limit,
)
from .services import DuplicateSubmission, publish_entry_once

PASSWORD = "Valid-community-password-4821!"


class WebTestCase(TestCase):
    def setUp(self):
        cache.clear()
        caches["recovery_notices"].clear()

    def create_member(self, username: str, **kwargs) -> User:
        user = User.objects.create_user(username=username, password=PASSWORD, **kwargs)
        Profile.objects.filter(user=user).update(display_name=username.title())
        user.profile.refresh_from_db()
        return user

    def open_site(self, **changes) -> SiteConfiguration:
        defaults = {"registration_mode": RegistrationMode.OPEN}
        defaults.update(changes)
        return SiteConfiguration.objects.create(pk=1, **defaults)

    def entry_nonce(self) -> str:
        response = self.client.get(reverse("web:entry-create"))
        return response.context["form"]["nonce"].value()

    def comment_nonce(self, entry: Entry) -> str:
        response = self.client.get(
            reverse("web:entry-detail", kwargs={"public_id": entry.public_id})
        )
        return response.context["comment_form"]["nonce"].value()


class PublicReadTests(WebTestCase):
    def setUp(self):
        super().setUp()
        self.author = self.create_member("writer", email="private@example.com")
        self.visible = Entry.objects.create(
            author=self.author, body="公开内容 <script>alert(1)</script>"
        )
        self.hidden = Entry.objects.create(
            author=self.author,
            body="隐藏内容",
            state=ContentState.HIDDEN,
        )
        self.pending = Entry.objects.create(
            author=self.author,
            body="待审核内容",
            state=ContentState.PENDING,
        )
        self.inactive_author = self.create_member("inactive", is_active=False)
        self.inactive_entry = Entry.objects.create(
            author=self.inactive_author,
            body="停用成员内容",
        )

    def test_home_renders_only_public_content_and_escapes_body(self):
        response = self.client.get(reverse("web:home"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "公开内容")
        self.assertNotContains(response, "隐藏内容")
        self.assertNotContains(response, "待审核内容")
        self.assertNotContains(response, "停用成员内容")
        self.assertContains(response, "&lt;script&gt;alert(1)&lt;/script&gt;", html=False)

    def test_public_pages_have_security_headers(self):
        response = self.client.get(reverse("web:home"))

        self.assertIn("default-src 'self'", response.headers["Content-Security-Policy"])
        self.assertEqual(response.headers["Referrer-Policy"], "strict-origin-when-cross-origin")
        self.assertEqual(response.headers["X-Frame-Options"], "DENY")

    def test_hidden_pending_and_inactive_entry_details_are_not_found(self):
        for entry in (self.hidden, self.pending, self.inactive_entry):
            with self.subTest(entry=entry.body):
                response = self.client.get(
                    reverse("web:entry-detail", kwargs={"public_id": entry.public_id})
                )
                self.assertEqual(response.status_code, 404)

    def test_detail_filters_nonpublic_comments_and_inactive_authors(self):
        commenter = self.create_member("commenter")
        inactive = self.create_member("silent", is_active=False)
        Comment.objects.create(entry=self.visible, author=commenter, body="公开评论")
        Comment.objects.create(
            entry=self.visible,
            author=commenter,
            body="隐藏评论",
            state=ContentState.HIDDEN,
        )
        Comment.objects.create(
            entry=self.visible,
            author=commenter,
            body="待审评论",
            state=ContentState.PENDING,
        )
        Comment.objects.create(entry=self.visible, author=inactive, body="停用评论")

        response = self.client.get(
            reverse("web:entry-detail", kwargs={"public_id": self.visible.public_id})
        )

        self.assertContains(response, "公开评论")
        self.assertNotContains(response, "隐藏评论")
        self.assertNotContains(response, "待审评论")
        self.assertNotContains(response, "停用评论")

    def test_profile_does_not_expose_private_account_fields(self):
        response = self.client.get(
            reverse("web:member-profile", kwargs={"public_id": self.author.public_id})
        )

        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, "private@example.com")
        self.assertNotContains(response, "last_login")
        self.assertNotContains(response, "is_staff")

    def test_inactive_profile_is_not_found(self):
        response = self.client.get(
            reverse(
                "web:member-profile",
                kwargs={"public_id": self.inactive_author.public_id},
            )
        )

        self.assertEqual(response.status_code, 404)

    def test_search_topic_and_following_filters(self):
        viewer = self.create_member("reader")
        other = self.create_member("other")
        python = Topic.objects.create(slug="python", label="Python")
        django_entry = Entry.objects.create(author=self.author, body="Django notes")
        django_entry.topics.add(python)
        Entry.objects.create(author=other, body="Other subject")
        Follow.objects.create(follower=viewer, followed=self.author)

        self.client.force_login(viewer)
        search = self.client.get(reverse("web:home"), {"q": "Django"})
        topic = self.client.get(reverse("web:home"), {"topic": "python"})
        following = self.client.get(reverse("web:home"), {"feed": "following"})

        self.assertContains(search, "Django notes")
        self.assertNotContains(search, "Other subject")
        self.assertContains(topic, "Django notes")
        self.assertContains(following, "Django notes")
        self.assertNotContains(following, "Other subject")


class AuthenticationUiTests(WebTestCase):
    def test_registration_links_to_readable_rules_and_privacy_pages(self):
        self.open_site()

        register_response = self.client.get(reverse("web:register"))

        self.assertContains(
            register_response,
            f'href="{reverse("web:community-rules")}"',
        )
        self.assertContains(
            register_response,
            f'href="{reverse("web:privacy-notice")}"',
        )
        for route_name, heading in (
            ("web:community-rules", "社区公约"),
            ("web:privacy-notice", "隐私说明"),
        ):
            with self.subTest(route_name=route_name):
                response = self.client.get(reverse(route_name))
                self.assertEqual(response.status_code, 200)
                self.assertContains(response, f"<h1>{heading}</h1>", html=True)

    def test_home_registration_copy_matches_open_invite_and_closed_modes(self):
        cases = (
            (RegistrationMode.OPEN, ("免费注册", "注册加入"), ("查看注册状态",)),
            (RegistrationMode.INVITE, ("使用邀请注册", "凭邀请加入"), ("免费注册",)),
            (RegistrationMode.CLOSED, ("查看注册状态",), ("免费注册", "注册加入")),
        )
        for mode, expected, excluded in cases:
            with self.subTest(mode=mode):
                SiteConfiguration.objects.update_or_create(
                    pk=1,
                    defaults={"registration_mode": mode},
                )
                response = self.client.get(reverse("web:home"))
                for text in expected:
                    self.assertContains(response, text)
                for text in excluded:
                    self.assertNotContains(response, text)

    def test_registration_entry_remains_visible_when_registration_is_closed(self):
        home_response = self.client.get(reverse("web:home"))
        login_response = self.client.get(reverse("web:login"))

        register_url = reverse("web:register")
        self.assertContains(home_response, f'href="{register_url}"')
        self.assertContains(home_response, "查看注册状态")
        self.assertContains(login_response, f'href="{register_url}"')
        self.assertContains(login_response, "查看注册状态")

    def test_registration_is_closed_by_default_for_get_and_post(self):
        get_response = self.client.get(reverse("web:register"))
        post_response = self.client.post(
            reverse("web:register"),
            {
                "username": "newmember",
                "email": "",
                "password1": PASSWORD,
                "password2": PASSWORD,
                "accept_rules": "on",
            },
        )

        self.assertContains(get_response, "注册暂未开放")
        self.assertEqual(post_response.status_code, 403)
        self.assertFalse(User.objects.filter(username="newmember").exists())

    def test_invite_mode_displays_the_invitation_field(self):
        SiteConfiguration.objects.create(pk=1, registration_mode=RegistrationMode.INVITE)

        response = self.client.get(reverse("web:register"))

        self.assertContains(response, "使用邀请加入")
        self.assertContains(response, "邀请码")

    def test_invite_registration_claims_the_token_and_logs_the_member_in(self):
        self.open_site(registration_mode=RegistrationMode.INVITE)
        owner = User.objects.create_superuser(username="owner")
        invitation, plaintext = issue_invitation(
            issuer=owner,
            expires_at=timezone.now() + timedelta(days=1),
        )

        response = self.client.post(
            reverse("web:register"),
            {
                "username": "invited-member",
                "email": "invited@example.test",
                "password1": PASSWORD,
                "password2": PASSWORD,
                "invitation_token": plaintext,
                "accept_rules": "on",
            },
        )

        self.assertRedirects(response, reverse("web:recovery-code"))
        invitation.refresh_from_db()
        self.assertEqual(invitation.claimed_by.username, "invited-member")
        self.assertEqual(int(self.client.session["_auth_user_id"]), invitation.claimed_by_id)
        self.assertTrue(Profile.objects.filter(user=invitation.claimed_by).exists())

    def test_open_registration_creates_user_profile_and_rotates_into_session(self):
        self.open_site()

        response = self.client.post(
            reverse("web:register"),
            {
                "username": "  NewMember  ",
                "email": " New@Example.com ",
                "password1": PASSWORD,
                "password2": PASSWORD,
                "accept_rules": "on",
            },
        )

        self.assertRedirects(response, reverse("web:recovery-code"))
        user = User.objects.get(username="NewMember")
        self.assertEqual(user.email, "new@example.com")
        self.assertTrue(Profile.objects.filter(user=user).exists())
        self.assertEqual(int(self.client.session["_auth_user_id"]), user.pk)

    def test_service_authorization_wins_if_registration_closes_after_page_render(self):
        stored = self.open_site()
        visible_snapshot = SiteConfiguration(
            pk=1,
            registration_mode=RegistrationMode.OPEN,
        )
        stored.registration_mode = RegistrationMode.CLOSED
        stored.save()

        with patch("meppp.web.views.get_site_configuration", return_value=visible_snapshot):
            response = self.client.post(
                reverse("web:register"),
                {
                    "username": "late-member",
                    "email": "late-member@example.test",
                    "password1": PASSWORD,
                    "password2": PASSWORD,
                    "accept_rules": "on",
                },
            )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "站点当前未开放注册")
        self.assertFalse(User.objects.filter(username="late-member").exists())

    def test_registration_duplicate_username_is_a_form_error(self):
        self.open_site()
        self.create_member("Member")

        response = self.client.post(
            reverse("web:register"),
            {
                "username": "member",
                "email": "another@example.test",
                "password1": PASSWORD,
                "password2": PASSWORD,
                "accept_rules": "on",
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "这个用户名已经被使用")
        self.assertEqual(User.objects.filter(username__iexact="member").count(), 1)

    def test_registration_requires_password_validation_and_rules(self):
        self.open_site()

        response = self.client.post(
            reverse("web:register"),
            {
                "username": "newmember",
                "email": "",
                "password1": "123",
                "password2": "123",
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertFalse(User.objects.filter(username="newmember").exists())

    def test_login_rejects_external_next_and_authenticated_page_is_not_cached(self):
        member = self.create_member("member")

        response = self.client.post(
            reverse("web:login"),
            {"username": member.username, "password": PASSWORD, "next": "https://evil.test/"},
        )
        home_response = self.client.get(reverse("web:home"))

        self.assertRedirects(response, reverse("web:home"))
        self.assertEqual(home_response.headers["Cache-Control"], "private, no-store")
        self.assertIn("Cookie", home_response.headers["Vary"])

    def test_logout_is_post_only_and_clears_session(self):
        member = self.create_member("member")
        self.client.force_login(member)

        get_response = self.client.get(reverse("web:logout"))
        post_response = self.client.post(reverse("web:logout"))

        self.assertEqual(get_response.status_code, 405)
        self.assertRedirects(post_response, reverse("web:home"))
        self.assertNotIn("_auth_user_id", self.client.session)

    def test_login_rate_limit_returns_429_without_authentication(self):
        member = self.create_member("member")
        with patch.dict(RATE_LIMITS, {"login": RateLimit(1, 60)}):
            self.client.post(
                reverse("web:login"),
                {"username": member.username, "password": "wrong"},
            )
            response = self.client.post(
                reverse("web:login"),
                {"username": member.username, "password": PASSWORD},
            )

        self.assertEqual(response.status_code, 429)
        self.assertEqual(response.headers["Retry-After"], "60")
        self.assertNotIn("_auth_user_id", self.client.session)

    def test_login_identity_normalization_blocks_compatibility_character_bypass(self):
        self.create_member("A")

        with patch.dict(RATE_LIMITS, {"login": RateLimit(1, 60)}):
            self.client.post(
                reverse("web:login"),
                {"username": "A", "password": "wrong"},
                REMOTE_ADDR="198.51.100.10",
            )
            response = self.client.post(
                reverse("web:login"),
                {"username": "Ａ", "password": PASSWORD},
                REMOTE_ADDR="198.51.100.11",
            )

        self.assertEqual(response.status_code, 429)
        self.assertNotIn("_auth_user_id", self.client.session)

    def test_registration_rate_limit_blocks_repeated_attempts_by_ip(self):
        self.open_site()
        with patch.dict(RATE_LIMITS, {"register": RateLimit(1, 60)}):
            self.client.post(
                reverse("web:register"),
                {
                    "username": "first-attempt",
                    "email": "",
                    "password1": "short",
                    "password2": "short",
                },
                REMOTE_ADDR="198.51.100.12",
            )
            response = self.client.post(
                reverse("web:register"),
                {
                    "username": "second-attempt",
                    "email": "",
                    "password1": PASSWORD,
                    "password2": PASSWORD,
                    "accept_rules": "on",
                },
                REMOTE_ADDR="198.51.100.12",
            )

        self.assertEqual(response.status_code, 429)
        self.assertEqual(response.headers["Retry-After"], "60")
        self.assertFalse(User.objects.filter(username="second-attempt").exists())
        self.assertNotIn("_auth_user_id", self.client.session)

    def test_registration_identity_normalization_blocks_compatibility_character_bypass(self):
        self.open_site()
        with patch.dict(RATE_LIMITS, {"register": RateLimit(1, 60)}):
            self.client.post(
                reverse("web:register"),
                {
                    "username": "A",
                    "email": "",
                    "password1": "short",
                    "password2": "short",
                },
                REMOTE_ADDR="198.51.100.21",
            )
            response = self.client.post(
                reverse("web:register"),
                {
                    "username": "Ａ",
                    "email": "",
                    "password1": PASSWORD,
                    "password2": PASSWORD,
                    "accept_rules": "on",
                },
                REMOTE_ADDR="198.51.100.22",
            )

        self.assertEqual(response.status_code, 429)
        self.assertFalse(User.objects.filter(username__iexact="A").exists())
        self.assertNotIn("_auth_user_id", self.client.session)


@override_settings(
    TRUST_PROXY=True,
    TRUSTED_PROXY_NETWORKS=(ip_network("10.0.0.0/8"),),
)
class ProxyRateLimitTests(WebTestCase):
    def request(self, *, remote: str, real_ip: str | None = None):
        headers = {"REMOTE_ADDR": remote}
        if real_ip is not None:
            headers["HTTP_X_REAL_IP"] = real_ip
        return RequestFactory().get("/", **headers)

    def test_trusted_proxy_clients_receive_independent_ip_buckets(self):
        first_client = self.request(remote="10.0.0.5", real_ip="198.51.100.10")
        second_client = self.request(remote="10.0.0.5", real_ip="198.51.100.11")

        with patch.dict(RATE_LIMITS, {"login": RateLimit(1, 60)}):
            enforce_rate_limit(first_client, scope="login")
            enforce_rate_limit(second_client, scope="login")
            with self.assertRaises(RateLimitExceeded):
                enforce_rate_limit(first_client, scope="login")

    def test_untrusted_client_cannot_spoof_forwarded_address(self):
        first = self.request(remote="203.0.113.8", real_ip="198.51.100.10")
        second = self.request(remote="203.0.113.8", real_ip="198.51.100.11")

        with patch.dict(RATE_LIMITS, {"login": RateLimit(1, 60)}):
            enforce_rate_limit(first, scope="login")
            with self.assertRaises(RateLimitExceeded):
                enforce_rate_limit(second, scope="login")

    def test_invalid_proxy_ip_fails_closed_to_the_gateway_bucket(self):
        missing = self.request(remote="10.0.0.5")
        chain = self.request(remote="10.0.0.5", real_ip="198.51.100.1, 198.51.100.2")
        invalid = self.request(remote="10.0.0.5", real_ip="not-an-ip")

        self.assertEqual(client_ip(missing), "10.0.0.5")
        self.assertEqual(client_ip(chain), "10.0.0.5")
        self.assertEqual(client_ip(invalid), "10.0.0.5")
        with patch.dict(RATE_LIMITS, {"login": RateLimit(1, 60)}):
            enforce_rate_limit(missing, scope="login")
            with self.assertRaises(RateLimitExceeded):
                enforce_rate_limit(chain, scope="login")


class PublishingUiTests(WebTestCase):
    def setUp(self):
        super().setUp()
        self.configuration = self.open_site()
        self.author = self.create_member("author")
        self.reader = self.create_member("reader")
        self.topic = Topic.objects.create(slug="build", label="构建")
        self.client.force_login(self.author)

    def test_write_requires_authentication(self):
        self.client.logout()

        response = self.client.get(reverse("web:entry-create"))

        self.assertEqual(response.status_code, 302)
        self.assertTrue(response.url.startswith(reverse("web:login")))

    def test_publish_uses_server_side_author_state_and_topics(self):
        nonce = self.entry_nonce()

        response = self.client.post(
            reverse("web:entry-create"),
            {
                "body": "  一条新的公开内容  ",
                "topics": [self.topic.pk],
                "nonce": nonce,
                "author": self.reader.pk,
                "state": ContentState.HIDDEN,
            },
        )

        entry = Entry.objects.get(body="一条新的公开内容")
        self.assertRedirects(
            response,
            reverse("web:entry-detail", kwargs={"public_id": entry.public_id}),
        )
        self.assertEqual(entry.author, self.author)
        self.assertEqual(entry.state, ContentState.PUBLISHED)
        self.assertEqual(list(entry.topics.all()), [self.topic])

    def test_publish_nonce_prevents_duplicate_submission(self):
        nonce = self.entry_nonce()
        payload = {"body": "只创建一次", "nonce": nonce}

        self.client.post(reverse("web:entry-create"), payload)
        response = self.client.post(reverse("web:entry-create"), payload)

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "已经处理过")
        self.assertEqual(Entry.objects.filter(body="只创建一次").count(), 1)

    def test_database_claim_blocks_two_session_snapshots_using_the_same_nonce(self):
        nonce = self.entry_nonce()

        publish_entry_once(
            author=self.author,
            body="并发请求只创建一次",
            topics=[],
            purpose="entry:create",
            token=nonce,
        )
        with self.assertRaises(DuplicateSubmission):
            publish_entry_once(
                author=self.author,
                body="并发请求只创建一次",
                topics=[],
                purpose="entry:create",
                token=nonce,
            )

        self.assertEqual(Entry.objects.filter(body="并发请求只创建一次").count(), 1)
        self.assertEqual(SubmissionClaim.objects.count(), 1)

    def test_publish_respects_dynamic_length(self):
        self.configuration.post_max_length = 100
        self.configuration.save()
        nonce = self.entry_nonce()

        response = self.client.post(
            reverse("web:entry-create"),
            {"body": "x" * 101, "nonce": nonce},
        )

        self.assertEqual(response.status_code, 200)
        self.assertFalse(Entry.objects.filter(author=self.author).exists())

    def test_premoderation_creates_pending_entry_not_public(self):
        self.configuration.moderation_mode = ModerationMode.PREMODERATION
        self.configuration.save()
        nonce = self.entry_nonce()

        response = self.client.post(
            reverse("web:entry-create"),
            {"body": "先审核再公开", "nonce": nonce},
            follow=True,
        )

        entry = Entry.objects.get(body="先审核再公开")
        self.assertEqual(entry.state, ContentState.PENDING)
        self.assertContains(response, "已提交审核")
        self.assertNotContains(response, "先审核再公开")

    def test_comment_and_notification_are_created_together(self):
        entry = Entry.objects.create(author=self.reader, body="欢迎评论")
        nonce = self.comment_nonce(entry)

        response = self.client.post(
            reverse("web:comment-create", kwargs={"public_id": entry.public_id}),
            {"body": "认真回应", "nonce": nonce},
        )

        self.assertEqual(response.status_code, 302)
        comment = Comment.objects.get(body="认真回应")
        notification = Notification.objects.get(kind=NotificationKind.COMMENT)
        self.assertEqual(comment.author, self.author)
        self.assertEqual(notification.recipient, self.reader)
        self.assertEqual(notification.actor, self.author)

    def test_comment_on_own_entry_does_not_notify_self(self):
        entry = Entry.objects.create(author=self.author, body="自己的内容")
        nonce = self.comment_nonce(entry)

        self.client.post(
            reverse("web:comment-create", kwargs={"public_id": entry.public_id}),
            {"body": "补充一句", "nonce": nonce},
        )

        self.assertTrue(Comment.objects.filter(body="补充一句").exists())
        self.assertFalse(Notification.objects.exists())

    def test_comment_is_blocked_when_disabled_or_entry_hidden(self):
        entry = Entry.objects.create(author=self.reader, body="原内容")
        nonce = self.comment_nonce(entry)
        self.configuration.comments_enabled = False
        self.configuration.save()

        disabled_response = self.client.post(
            reverse("web:comment-create", kwargs={"public_id": entry.public_id}),
            {"body": "不应写入", "nonce": nonce},
            follow=True,
        )

        self.assertContains(disabled_response, "关闭评论")
        self.assertFalse(Comment.objects.exists())

        self.configuration.comments_enabled = True
        self.configuration.save()
        second_nonce = self.comment_nonce(entry)
        Entry.objects.filter(pk=entry.pk).update(state=ContentState.HIDDEN)
        hidden_response = self.client.post(
            reverse("web:comment-create", kwargs={"public_id": entry.public_id}),
            {"body": "也不应写入", "nonce": second_nonce},
            follow=True,
        )
        self.assertEqual(hidden_response.status_code, 404)
        self.assertFalse(Comment.objects.exists())

    def test_premoderated_comment_is_pending_and_does_not_notify(self):
        self.configuration.moderation_mode = ModerationMode.PREMODERATION
        self.configuration.save()
        entry = Entry.objects.create(author=self.reader, body="原内容")
        nonce = self.comment_nonce(entry)

        self.client.post(
            reverse("web:comment-create", kwargs={"public_id": entry.public_id}),
            {"body": "等待审核", "nonce": nonce},
        )

        self.assertEqual(Comment.objects.get().state, ContentState.PENDING)
        self.assertFalse(Notification.objects.exists())

    def test_write_endpoint_requires_csrf(self):
        csrf_client = Client(enforce_csrf_checks=True)
        csrf_client.force_login(self.author)

        response = csrf_client.post(reverse("web:entry-create"), {"body": "blocked"})

        self.assertEqual(response.status_code, 403)
        self.assertFalse(Entry.objects.filter(body="blocked").exists())


class SocialUiTests(WebTestCase):
    def setUp(self):
        super().setUp()
        self.actor = self.create_member("actor")
        self.author = self.create_member("author")
        self.entry = Entry.objects.create(author=self.author, body="可互动内容")
        self.client.force_login(self.actor)

    def test_like_is_explicit_idempotent_and_notifies_once(self):
        url = reverse("web:entry-like", kwargs={"public_id": self.entry.public_id})

        self.client.post(url, {"state": "on"})
        self.client.post(url, {"state": "on"})

        self.assertEqual(EntryLike.objects.count(), 1)
        self.assertEqual(Notification.objects.filter(kind=NotificationKind.LIKE).count(), 1)

        self.client.post(url, {"state": "off"})
        self.assertFalse(EntryLike.objects.exists())

    def test_like_get_is_rejected_and_hidden_entry_cannot_be_liked(self):
        url = reverse("web:entry-like", kwargs={"public_id": self.entry.public_id})
        get_response = self.client.get(url)
        Entry.objects.filter(pk=self.entry.pk).update(state=ContentState.HIDDEN)
        post_response = self.client.post(url, {"state": "on"}, follow=True)

        self.assertEqual(get_response.status_code, 405)
        self.assertEqual(post_response.status_code, 404)
        self.assertFalse(EntryLike.objects.exists())

    def test_follow_is_explicit_idempotent_and_notifies_once(self):
        url = reverse("web:member-follow", kwargs={"public_id": self.author.public_id})

        self.client.post(url, {"state": "on"})
        self.client.post(url, {"state": "on"})

        self.assertEqual(Follow.objects.count(), 1)
        self.assertEqual(Notification.objects.filter(kind=NotificationKind.FOLLOW).count(), 1)

        self.client.post(url, {"state": "off"})
        self.assertFalse(Follow.objects.exists())

    def test_self_follow_and_inactive_target_are_rejected(self):
        self_url = reverse("web:member-follow", kwargs={"public_id": self.actor.public_id})
        inactive = self.create_member("inactive", is_active=False)
        inactive_url = reverse("web:member-follow", kwargs={"public_id": inactive.public_id})

        self_response = self.client.post(self_url, {"state": "on"}, follow=True)
        inactive_response = self.client.post(inactive_url, {"state": "on"}, follow=True)

        self.assertContains(self_response, "不能关注自己")
        self.assertEqual(inactive_response.status_code, 404)
        self.assertFalse(Follow.objects.exists())


class ModerationAndNotificationUiTests(WebTestCase):
    def setUp(self):
        super().setUp()
        self.reporter = self.create_member("reporter")
        self.author = self.create_member("author")
        self.entry = Entry.objects.create(author=self.author, body="需要复核的内容")
        self.client.force_login(self.reporter)

    def test_report_is_bound_to_visible_target_and_duplicate_is_friendly(self):
        url = reverse("web:report-entry", kwargs={"public_id": self.entry.public_id})

        first = self.client.post(url, {"reason": ReportReason.SPAM, "details": ""})
        second = self.client.post(
            url,
            {"reason": ReportReason.HARASSMENT, "details": "重复提交"},
            follow=True,
        )

        self.assertEqual(first.status_code, 302)
        self.assertEqual(Report.objects.count(), 1)
        self.assertContains(second, "已经提交过")

    def test_other_report_reason_requires_details(self):
        response = self.client.post(
            reverse("web:report-entry", kwargs={"public_id": self.entry.public_id}),
            {"reason": ReportReason.OTHER, "details": ""},
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "请补充说明")
        self.assertFalse(Report.objects.exists())

    def test_report_return_url_rejects_external_and_unsafe_schemes(self):
        url = reverse("web:report-entry", kwargs={"public_id": self.entry.public_id})
        fallback = reverse("web:entry-detail", kwargs={"public_id": self.entry.public_id})

        for unsafe in ("https://evil.test/leave", "//evil.test/leave", "javascript:alert(1)"):
            with self.subTest(unsafe=unsafe):
                response = self.client.get(url, {"next": unsafe})
                self.assertEqual(response.context["return_url"], fallback)
                self.assertNotContains(response, unsafe)

        invalid_post = self.client.post(
            url,
            {
                "reason": ReportReason.OTHER,
                "details": "",
                "next": "https://evil.test/leave",
            },
        )
        self.assertEqual(invalid_post.status_code, 200)
        self.assertEqual(invalid_post.context["return_url"], fallback)
        self.assertNotContains(invalid_post, "https://evil.test/leave")

        valid_post = self.client.post(
            url,
            {
                "reason": ReportReason.SPAM,
                "details": "",
                "next": "https://evil.test/leave",
            },
        )
        self.assertRedirects(valid_post, fallback)

    def test_self_and_hidden_targets_cannot_be_reported(self):
        own = Entry.objects.create(author=self.reporter, body="自己的内容")
        own_response = self.client.get(
            reverse("web:report-entry", kwargs={"public_id": own.public_id})
        )
        Entry.objects.filter(pk=self.entry.pk).update(state=ContentState.HIDDEN)
        hidden_response = self.client.get(
            reverse("web:report-entry", kwargs={"public_id": self.entry.public_id})
        )

        self.assertEqual(own_response.status_code, 404)
        self.assertEqual(hidden_response.status_code, 404)

    def test_notifications_are_recipient_scoped_and_marked_read_by_post(self):
        other = self.create_member("other")
        own_notification = Notification.objects.create(
            recipient=self.reporter,
            actor=self.author,
            kind=NotificationKind.FOLLOW,
            target_type="user",
            target_public_id=self.author.public_id,
        )
        Notification.objects.create(
            recipient=other,
            actor=self.author,
            kind=NotificationKind.LIKE,
        )

        page = self.client.get(reverse("web:notifications"))
        read_response = self.client.post(reverse("web:notifications-read"))

        self.assertContains(page, "关注了你")
        self.assertNotContains(page, "赞了你的内容")
        self.assertRedirects(read_response, reverse("web:notifications"))
        own_notification.refresh_from_db()
        self.assertIsNotNone(own_notification.read_at)


@override_settings(DEBUG=False)
class ErrorPageTests(WebTestCase):
    def test_custom_not_found_page_is_rendered(self):
        response = self.client.get("/entry/00000000-0000-0000-0000-000000000000/")

        self.assertEqual(response.status_code, 404)
        self.assertContains(response, "这页不在公开记录里", status_code=404)
