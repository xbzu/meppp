from __future__ import annotations

import os
import re
from datetime import timedelta
from pathlib import Path

from django.contrib.staticfiles.testing import StaticLiveServerTestCase
from django.utils import timezone
from playwright.sync_api import expect, sync_playwright

from meppp.accounts.models import Profile, User
from meppp.configuration.models import (
    ModerationMode,
    RegistrationMode,
    SiteConfiguration,
)
from meppp.moderation.models import Report
from meppp.publishing.models import ContentState, Entry, Topic

PASSWORD = "Browser-test-password-4821!"
RESULTS_DIR = Path("test-results")

# Playwright's synchronous driver owns an event loop in this test thread. Database
# operations remain sequential; this test-only flag is never part of runtime settings.
os.environ["DJANGO_ALLOW_ASYNC_UNSAFE"] = "true"


class PublicUiBrowserTests(StaticLiveServerTestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.playwright = sync_playwright().start()
        cls.browser = cls.playwright.chromium.launch(headless=True)

    @classmethod
    def tearDownClass(cls):
        cls.browser.close()
        cls.playwright.stop()
        super().tearDownClass()

    def setUp(self):
        SiteConfiguration.objects.create(
            pk=1,
            site_name="MEPPP",
            tagline="把值得讨论的事情，写清楚。",
            registration_mode=RegistrationMode.OPEN,
        )
        self.author = User.objects.create_user(
            username="lin",
            password=PASSWORD,
            email="lin@example.test",
        )
        Profile.objects.filter(user=self.author).update(
            display_name="林木",
            bio="关注小型社区、独立软件和清楚表达。",
        )
        self.reporter = User.objects.create_user(username="reader", password=PASSWORD)
        Profile.objects.filter(user=self.reporter).update(display_name="读者")
        self.topic = Topic.objects.create(slug="indie-web", label="独立网络")
        self.entry = Entry.objects.create(
            author=self.author,
            body="小社区不需要追赶每一种功能。先把表达、回应和管理这三个环节做稳。",
        )
        self.entry.topics.add(self.topic)

        RESULTS_DIR.mkdir(exist_ok=True)
        self.context = self.browser.new_context(viewport={"width": 1440, "height": 1000})
        self.context.tracing.start(screenshots=True, snapshots=True, sources=True)
        self.page = self.context.new_page()
        self.console_errors: list[str] = []
        self.page_errors: list[str] = []
        self.failed_requests: list[str] = []
        self.bad_responses: list[str] = []
        self.page.on(
            "console",
            lambda message: (
                self.console_errors.append(message.text) if message.type == "error" else None
            ),
        )
        self.page.on("pageerror", lambda error: self.page_errors.append(str(error)))
        self.page.on("requestfailed", lambda request: self.failed_requests.append(request.url))
        self.page.on(
            "response",
            lambda response: (
                self.bad_responses.append(f"{response.status} {response.url}")
                if response.status >= 400
                else None
            ),
        )

    def tearDown(self):
        if not hasattr(self, "context"):
            return
        test_name = self.id().rsplit(".", 1)[-1]
        if hasattr(self, "page") and not self.page.is_closed():
            self.page.screenshot(path=RESULTS_DIR / f"{test_name}.png", full_page=True)
        self.context.tracing.stop(path=RESULTS_DIR / f"{test_name}.zip")
        self.context.close()

    def open(self, path: str):
        self.page.goto(f"{self.live_server_url}{path}")
        self.page.wait_for_load_state("networkidle")

    def login(self, username: str):
        self.open("/login/")
        username_field = self.page.get_by_label("用户名")
        username_field.focus()
        focus_style = username_field.evaluate(
            "element => ({style: getComputedStyle(element).outlineStyle, "
            "width: parseFloat(getComputedStyle(element).outlineWidth)})"
        )
        self.assertEqual(focus_style["style"], "solid")
        self.assertGreaterEqual(focus_style["width"], 3)
        username_field.fill(username)
        self.page.get_by_label("密码").fill(PASSWORD)
        self.page.get_by_role("button", name="登录", exact=True).click()
        self.page.wait_for_load_state("networkidle")

    def assert_browser_clean(self):
        fits_viewport = self.page.locator("html").evaluate(
            "element => element.scrollWidth <= element.clientWidth"
        )
        self.assertTrue(fits_viewport, "页面出现横向溢出")
        self.assertEqual(self.console_errors, [])
        self.assertEqual(self.page_errors, [])
        self.assertEqual(self.failed_requests, [])
        self.assertEqual(self.bad_responses, [])

    def test_desktop_public_feed_search_and_profile(self):
        self.open("/")

        expect(self.page).to_have_title(re.compile("MEPPP"))
        expect(self.page.get_by_role("heading", name="把值得讨论的事情，写清楚。")).to_be_visible()
        expect(self.page.get_by_text("小社区不需要追赶每一种功能")).to_be_visible()
        self.page.screenshot(path=RESULTS_DIR / "public-home-desktop.png", full_page=True)
        self.page.get_by_label("搜索社区").fill("小社区")
        self.page.get_by_role("button", name="搜索").click()
        self.page.wait_for_load_state("networkidle")
        expect(self.page.get_by_text("小社区不需要追赶每一种功能")).to_be_visible()
        self.page.get_by_role("link", name="林木").first.click()
        self.page.wait_for_load_state("networkidle")
        expect(self.page.get_by_role("heading", name="林木")).to_be_visible()
        expect(self.page.get_by_text("lin@example.test")).to_have_count(0)
        self.assert_browser_clean()

    def test_member_can_publish_comment_and_like_without_javascript_errors(self):
        self.login("lin")
        self.page.get_by_role("link", name="写一条").click()
        self.page.wait_for_load_state("networkidle")
        self.page.get_by_label("正文").fill("浏览器验证：这是一条由真实成员流程发布的内容。")
        self.page.get_by_label("独立网络").check()
        self.page.get_by_role("button", name="发布内容").click()
        self.page.wait_for_load_state("networkidle")

        expect(self.page).to_have_url(re.compile(r"/entry/[0-9a-f-]+/"))
        expect(
            self.page.get_by_text("浏览器验证：这是一条由真实成员流程发布的内容。")
        ).to_be_visible()
        self.page.get_by_role("button", name=re.compile(r"赞 0")).click()
        self.page.wait_for_load_state("networkidle")
        self.page.get_by_label("写下评论").fill("评论流程也已经通过浏览器。")
        self.page.get_by_role("button", name="发表讨论").click()
        self.page.wait_for_load_state("networkidle")
        expect(self.page.get_by_text("评论流程也已经通过浏览器。")).to_be_visible()
        self.assert_browser_clean()

    def test_member_can_report_bound_entry(self):
        self.login("reader")
        self.page.get_by_role("link", name="举报").first.click()
        self.page.wait_for_load_state("networkidle")
        expect(self.page.get_by_role("heading", name="向管理员提交举报")).to_be_visible()
        self.page.get_by_label("举报原因").select_option("spam")
        self.page.get_by_role("button", name="确认提交").click()
        self.page.wait_for_load_state("networkidle")

        expect(self.page.get_by_text("举报已提交", exact=False)).to_be_visible()
        self.assertEqual(Report.objects.count(), 1)
        self.assert_browser_clean()

    def test_owner_can_reach_branded_site_configuration_admin(self):
        User.objects.create_superuser(
            username="owner",
            password=PASSWORD,
            email="owner@example.test",
        )
        self.page.goto(
            f"{self.live_server_url}/admin/login/"
            "?next=/admin/configuration/siteconfiguration/1/change/"
        )
        expect(self.page.locator("#id_username")).to_be_visible()
        self.page.locator("#id_username").fill("OWNER")
        self.page.locator("#id_password").fill(PASSWORD)
        self.page.get_by_role("button", name="登录").click()

        expect(self.page).to_have_url(
            re.compile(r"/admin/configuration/siteconfiguration/1/change/")
        )
        expect(self.page.get_by_text("MEPPP 管理后台", exact=True)).to_be_visible()
        expect(self.page.locator("#id_site_name")).to_have_value("MEPPP")
        expect(self.page.locator("#id_registration_mode")).to_be_visible()
        expect(self.page.locator("#id_moderation_mode")).to_be_visible()
        expect(self.page.locator("#id_comments_enabled")).to_be_visible()
        self.page.screenshot(path=RESULTS_DIR / "admin-configuration-desktop.png", full_page=True)
        self.assert_browser_clean()

    def test_invited_member_moderation_and_withdrawal_complete_operator_loop(self):
        configuration = SiteConfiguration.objects.get(pk=1)
        configuration.registration_mode = RegistrationMode.INVITE
        configuration.moderation_mode = ModerationMode.PREMODERATION
        configuration.save(update_fields=("registration_mode", "moderation_mode", "updated_at"))
        User.objects.create_superuser(
            username="owner",
            password=PASSWORD,
            email="owner@example.test",
        )

        self.page.goto(f"{self.live_server_url}/admin/login/?next=/admin/accounts/invitation/add/")
        self.page.locator("#id_username").fill("owner")
        self.page.locator("#id_password").fill(PASSWORD)
        self.page.get_by_role("button", name="登录").click()
        expect(self.page).to_have_url(re.compile(r"/admin/accounts/invitation/add/"))
        self.page.get_by_label("绑定邮箱").fill("invitee@example.test")
        expires_at = timezone.localtime(timezone.now() + timedelta(days=3)).strftime(
            "%Y-%m-%dT%H:%M"
        )
        self.page.get_by_label("有效期至").fill(expires_at)
        self.page.get_by_role("button", name="签发邀请").click()
        invitation_token = self.page.locator("#issued-invitation-token").input_value()
        self.assertGreaterEqual(len(invitation_token), 43)
        copy_button = self.page.get_by_role("button", name="复制邀请码")
        for locator in (self.page.locator("#issued-invitation-token"), copy_button):
            box = locator.bounding_box()
            self.assertIsNotNone(box)
            self.assertGreaterEqual(box["height"], 44)
        copy_button.click()
        expect(self.page.locator("#invitation-copy-status")).to_have_text(
            re.compile("已复制|已选中")
        )

        self.context.clear_cookies()
        self.open("/join/")
        expect(self.page.get_by_role("heading", name="使用邀请加入")).to_be_visible()
        self.page.get_by_label("用户名").fill("invitee")
        self.page.get_by_label("邮箱").fill("invitee@example.test")
        self.page.get_by_label("邀请码").fill(invitation_token)
        self.page.get_by_label("密码", exact=True).fill(PASSWORD)
        self.page.get_by_label("确认密码").fill(PASSWORD)
        self.page.get_by_label("我愿意遵守社区公约").check()
        self.page.get_by_role("button", name="加入社区").click()
        self.page.wait_for_load_state("networkidle")
        expect(self.page.get_by_text("欢迎回来，invitee", exact=False)).to_be_visible()

        self.page.get_by_role("link", name="写一条").click()
        self.page.get_by_label("正文").fill("邀请制审核闭环：这条内容先进入待审队列。")
        self.page.get_by_role("button", name="发布内容").click()
        self.page.wait_for_load_state("networkidle")
        expect(self.page.get_by_text("内容已提交审核", exact=False)).to_be_visible()
        self.page.get_by_role("link", name="我的", exact=True).click()
        expect(self.page.get_by_text("邀请制审核闭环：这条内容先进入待审队列。")).to_be_visible()
        expect(
            self.page.get_by_role("region", name="我的内容").get_by_text("待审核", exact=True)
        ).to_be_visible()
        pending_entry = Entry.objects.get(author__username="invitee")
        self.assertEqual(pending_entry.state, ContentState.PENDING)

        self.context.clear_cookies()
        self.page.goto(f"{self.live_server_url}/admin/login/?next=/admin/operations/")
        self.page.locator("#id_username").fill("owner")
        self.page.locator("#id_password").fill(PASSWORD)
        self.page.get_by_role("button", name="登录").click()
        expect(self.page.get_by_role("heading", name="运营总览")).to_be_visible()
        self.page.locator(".operations-links").get_by_role(
            "link", name=re.compile("待审内容")
        ).click()
        expect(self.page.get_by_text("邀请制审核闭环：这条内容先进入待审队列。")).to_be_visible()
        self.page.get_by_role("link", name="立即审核").click()
        self.page.get_by_label("批准公开").check()
        self.page.get_by_label("审核理由").fill("表达清楚，符合社区公约。")
        self.page.get_by_label("我已核对内容、作者和审核结论").check()
        review_submit = self.page.get_by_role("button", name="提交审核决定")
        review_back = self.page.get_by_role("link", name="返回待审队列")
        for locator in (review_submit, review_back):
            box = locator.bounding_box()
            self.assertIsNotNone(box)
            self.assertGreaterEqual(box["height"], 44)
        review_submit.click()
        self.page.wait_for_load_state("networkidle")
        expect(self.page.get_by_text("内容已完成审核", exact=False)).to_be_visible()
        self.page.screenshot(path=RESULTS_DIR / "operator-review-complete.png", full_page=True)

        pending_entry.refresh_from_db()
        self.assertEqual(pending_entry.state, ContentState.PUBLISHED)

        self.context.clear_cookies()
        self.login("invitee")
        self.page.get_by_role("link", name=re.compile("通知")).click()
        expect(self.page.get_by_text("你的内容")).to_be_visible()
        expect(self.page.get_by_text("已通过审核")).to_be_visible()
        expect(self.page.get_by_text("审核说明：表达清楚，符合社区公约。")).to_be_visible()
        self.page.get_by_role("link", name="我的", exact=True).click()
        expect(self.page.get_by_text("已发布", exact=True)).to_be_visible()
        withdrawal_control = self.page.get_by_text("撤回", exact=True)
        withdrawal_box = withdrawal_control.bounding_box()
        self.assertIsNotNone(withdrawal_box)
        self.assertGreaterEqual(withdrawal_box["width"], 44)
        self.assertGreaterEqual(withdrawal_box["height"], 44)
        withdrawal_control.click()
        withdrawal_confirm = self.page.get_by_role("button", name="确认撤回")
        confirm_box = withdrawal_confirm.bounding_box()
        self.assertIsNotNone(confirm_box)
        self.assertGreaterEqual(confirm_box["height"], 44)
        withdrawal_confirm.click()
        self.page.wait_for_load_state("networkidle")
        expect(self.page.get_by_text("内容已撤回", exact=False)).to_be_visible()
        expect(
            self.page.get_by_role("region", name="我的内容").get_by_text("已撤回", exact=True)
        ).to_be_visible()
        pending_entry.refresh_from_db()
        self.assertEqual(pending_entry.state, ContentState.DELETED)
        self.page.screenshot(
            path=RESULTS_DIR / "member-record-after-withdrawal.png",
            full_page=True,
        )
        self.page.set_viewport_size({"width": 390, "height": 844})
        expect(self.page.get_by_role("heading", name="我的社区")).to_be_visible()
        self.page.screenshot(
            path=RESULTS_DIR / "member-record-mobile.png",
            full_page=True,
        )
        self.assert_browser_clean()

    def test_mobile_registration_and_feed_do_not_overflow(self):
        self.context.tracing.stop()
        self.context.close()
        self.context = self.browser.new_context(viewport={"width": 390, "height": 844})
        self.context.tracing.start(screenshots=True, snapshots=True, sources=True)
        self.page = self.context.new_page()
        self.console_errors = []
        self.page_errors = []
        self.failed_requests = []
        self.bad_responses = []
        self.page.on(
            "console",
            lambda message: (
                self.console_errors.append(message.text) if message.type == "error" else None
            ),
        )
        self.page.on("pageerror", lambda error: self.page_errors.append(str(error)))
        self.page.on("requestfailed", lambda request: self.failed_requests.append(request.url))
        self.page.on(
            "response",
            lambda response: (
                self.bad_responses.append(f"{response.status} {response.url}")
                if response.status >= 400
                else None
            ),
        )

        self.open("/join/")
        self.page.get_by_label("用户名").fill("mobile")
        self.page.get_by_label("邮箱").fill("mobile@example.test")
        self.page.get_by_label("密码", exact=True).fill(PASSWORD)
        self.page.get_by_label("确认密码").fill(PASSWORD)
        self.page.get_by_label("我愿意遵守社区公约").check()
        self.page.get_by_role("button", name="加入社区").click()
        self.page.wait_for_load_state("networkidle")

        expect(self.page.get_by_text("欢迎回来，mobile", exact=False)).to_be_visible()
        expect(self.page.get_by_role("link", name=re.compile("通知"))).to_be_visible()
        self.assert_browser_clean()
