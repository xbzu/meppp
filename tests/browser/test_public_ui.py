from __future__ import annotations

import os
import re
import shutil
import subprocess
import tempfile
from datetime import timedelta
from io import BytesIO
from pathlib import Path
from unittest.mock import patch

from django.contrib.staticfiles.testing import StaticLiveServerTestCase
from django.utils import timezone
from PIL import Image
from playwright.sync_api import expect, sync_playwright

from meppp.accounts.models import Profile, User
from meppp.configuration.models import (
    ModerationMode,
    RegistrationMode,
    SiteConfiguration,
)
from meppp.external.models import MetadataStatus
from meppp.moderation.models import Report
from meppp.publishing.models import ContentState, Entry, Topic

PASSWORD = "Browser-test-password-4821!"
RESULTS_DIR = Path("test-results")

# Playwright's synchronous driver owns an event loop in this test thread. Database
# operations remain sequential; this test-only flag is never part of runtime settings.
os.environ["DJANGO_ALLOW_ASYNC_UNSAFE"] = "true"


def browser_image_payload(
    *,
    name: str,
    color: str,
    image_format: str = "JPEG",
    size: tuple[int, int] = (120, 80),
):
    content = BytesIO()
    Image.new("RGB", size, color).save(content, format=image_format)
    mime_type = "image/png" if image_format == "PNG" else "image/jpeg"
    return {"name": name, "mimeType": mime_type, "buffer": content.getvalue()}


def browser_video_payload():
    with tempfile.TemporaryDirectory() as temporary_directory:
        destination = Path(temporary_directory, "clip.mp4")
        result = subprocess.run(
            [
                "ffmpeg",
                "-nostdin",
                "-hide_banner",
                "-loglevel",
                "error",
                "-y",
                "-f",
                "lavfi",
                "-i",
                "color=c=blue:s=96x64:r=10:d=0.6",
                "-f",
                "lavfi",
                "-i",
                "sine=frequency=440:duration=0.6",
                "-shortest",
                "-c:v",
                "libx264",
                "-pix_fmt",
                "yuv420p",
                "-c:a",
                "aac",
                "-movflags",
                "+faststart",
                str(destination),
            ],
            stdin=subprocess.DEVNULL,
            capture_output=True,
            check=False,
            shell=False,
            timeout=20,
        )
        if result.returncode != 0:
            raise RuntimeError(result.stderr.decode(errors="replace"))
        return {"name": "clip.mp4", "mimeType": "video/mp4", "buffer": destination.read_bytes()}


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
        expect(self.page.get_by_role("heading", name="广场", exact=True)).to_be_visible()
        expect(self.page.locator(".paopao-shell")).to_be_visible()
        expect(self.page.locator(".stream-panel")).to_be_visible()
        expect(self.page.get_by_text("小社区不需要追赶每一种功能")).to_be_visible()
        expect(self.page.get_by_role("link", name="免费注册")).to_be_visible()
        expect(self.page.get_by_text("免费注册后可发文字", exact=False)).to_be_visible()
        self.page.screenshot(path=RESULTS_DIR / "public-home-desktop.png", full_page=True)
        rightbar_search = self.page.locator(".rightbar-search")
        rightbar_search.get_by_label("搜索社区").fill("小社区")
        rightbar_search.get_by_label("搜索社区").press("Enter")
        self.page.wait_for_load_state("networkidle")
        expect(self.page.get_by_text("小社区不需要追赶每一种功能")).to_be_visible()
        self.page.get_by_role("link", name="林木").first.click()
        self.page.wait_for_load_state("networkidle")
        expect(self.page.get_by_role("heading", name="林木")).to_be_visible()
        expect(self.page.get_by_text("lin@example.test")).to_have_count(0)
        self.assert_browser_clean()

    def test_closed_registration_stays_discoverable_and_explains_status(self):
        configuration = SiteConfiguration.objects.get(pk=1)
        configuration.registration_mode = RegistrationMode.CLOSED
        configuration.save(update_fields=("registration_mode", "updated_at"))

        self.open("/")
        register_link = self.page.locator(".guest-composer").get_by_role(
            "link", name="查看注册状态"
        )
        expect(register_link).to_be_visible()
        register_link.click()
        self.page.wait_for_load_state("networkidle")

        expect(self.page).to_have_url(re.compile(r"/join/$"))
        expect(self.page.get_by_role("heading", name="注册暂未开放")).to_be_visible()
        self.page.screenshot(path=RESULTS_DIR / "registration-closed-desktop.png", full_page=True)
        self.assert_browser_clean()

    def test_tablet_feed_uses_paopao_drawer_without_overflow(self):
        self.page.set_viewport_size({"width": 820, "height": 900})
        self.open("/")

        expect(self.page.locator(".feed-panel")).to_be_visible()
        expect(self.page.locator(".community-sidebar")).to_be_hidden()
        expect(self.page.locator(".discover-rail")).to_be_hidden()
        menu = self.page.get_by_label("打开导航")
        expect(menu).to_be_visible()
        menu.click()
        expect(self.page.locator("#mobile-discovery")).to_be_visible()
        self.page.screenshot(path=RESULTS_DIR / "public-home-tablet.png", full_page=True)
        self.assert_browser_clean()

    def test_member_can_publish_comment_and_like_without_javascript_errors(self):
        self.login("lin")
        self.page.get_by_role("link", name="写一条").click()
        self.page.wait_for_load_state("networkidle")
        self.page.get_by_label("正文").fill("浏览器验证：这是一条由真实成员流程发布的内容。")
        self.page.get_by_role("link", name="话题", exact=True).click()
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

    def test_member_can_publish_four_safe_images_on_mobile(self):
        self.login("lin")
        self.page.set_viewport_size({"width": 390, "height": 844})
        image_entry = self.page.get_by_role("link", name="发布图片")
        expect(image_entry).to_be_visible()
        self.page.screenshot(path=RESULTS_DIR / "member-home-mobile.png", full_page=True)
        image_entry.click()
        self.page.wait_for_load_state("networkidle")
        expect(self.page).to_have_url(re.compile(r"/write/\?compose=image$"))
        self.assertEqual(self.page.evaluate("window.scrollY"), 0)
        expect(self.page.get_by_role("navigation", name="选择发布方式")).to_be_visible()
        expect(self.page.locator("#composer-images")).to_be_visible()
        expect(self.page.locator("#composer-video")).to_be_hidden()
        expect(self.page.locator("#composer-source")).to_be_hidden()
        payloads = [
            browser_image_payload(name="one.jpg", color="firebrick"),
            browser_image_payload(name="two.png", color="navy", image_format="PNG"),
            browser_image_payload(name="three.jpg", color="olive"),
            browser_image_payload(name="four.jpg", color="purple"),
        ]
        self.page.get_by_label("选择图片").set_input_files(payloads)
        expect(self.page.locator("[data-image-preview-item]")).to_have_count(4)
        expect(self.page.locator("[data-image-status]")).to_contain_text("已选择 4 / 4 张")
        remove_buttons = self.page.get_by_role("button", name=re.compile(r"移除图片 \d："))
        expect(remove_buttons).to_have_count(4)
        for locator in remove_buttons.all():
            box = locator.bounding_box()
            self.assertIsNotNone(box)
            self.assertGreaterEqual(box["height"], 44)

        expect(self.page.get_by_label("图片 1（one.jpg）的替代文本（选填）")).to_be_visible()

        alt_inputs = self.page.locator("[data-image-alt]")
        for index, value in enumerate(("红色记录", "蓝色记录", "", "紫色记录")):
            alt_inputs.nth(index).fill(value)

        self.page.get_by_role("button", name="移除图片 1：one.jpg").click()
        expect(self.page.locator("[data-image-preview-item]")).to_have_count(3)
        self.page.get_by_label("选择图片").set_input_files(payloads)
        for index, value in enumerate(("红色记录", "蓝色记录", "", "紫色记录")):
            self.page.locator("[data-image-alt]").nth(index).fill(value)

        self.page.evaluate("window.scrollTo(0, 0)")
        self.page.screenshot(path=RESULTS_DIR / "composer-images-mobile.png", full_page=True)

        self.assertTrue(
            self.page.locator("html").evaluate(
                "element => element.scrollWidth <= element.clientWidth"
            )
        )
        self.page.get_by_role("button", name="发布内容").click()
        self.page.wait_for_load_state("networkidle")

        expect(self.page).to_have_url(re.compile(r"/entry/[0-9a-f-]+/"))
        expect(self.page.locator(".media-count-4")).to_be_visible()
        expect(self.page.locator("[data-entry-image]")).to_have_count(4)
        published_entry = Entry.objects.get(author=self.author, body="")
        attachment_evidence = []
        for attachment in published_entry.attachments.all():
            expected_name = f"entries/{published_entry.public_id}/{attachment.public_id}.webp"
            attachment_evidence.append(
                (
                    str(attachment.public_id),
                    attachment.file.name,
                    attachment.byte_size,
                    Path(attachment.file.path).stat().st_size,
                )
            )
            self.assertEqual(attachment.file.name, expected_name)
            self.assertEqual(Path(attachment.file.path).stat().st_size, attachment.byte_size)
        image_widths = self.page.locator("[data-entry-image]").evaluate_all(
            "images => images.map(image => image.naturalWidth)"
        )
        self.assertTrue(
            all(width > 0 for width in image_widths),
            f"widths={image_widths}; bad={self.bad_responses}; attachments={attachment_evidence}",
        )
        self.assertEqual(
            self.page.locator("[data-entry-image]").evaluate_all(
                "images => images.map(image => image.getAttribute('alt'))"
            ),
            ["红色记录", "蓝色记录", "", "紫色记录"],
        )
        self.page.screenshot(path=RESULTS_DIR / "safe-images-mobile.png", full_page=True)
        self.assert_browser_clean()

    def test_single_portrait_image_is_not_cropped_on_desktop(self):
        self.login("lin")
        self.page.get_by_role("link", name="写一条").click()
        self.page.wait_for_load_state("networkidle")
        self.page.get_by_label("正文").fill("极窄竖图也应完整展示。")
        self.page.get_by_role("link", name="图片", exact=True).click()
        self.page.get_by_label("选择图片").set_input_files(
            browser_image_payload(
                name="portrait.jpg",
                color="teal",
                size=(100, 800),
            )
        )
        self.page.get_by_label("图片 1（portrait.jpg）的替代文本（选填）").fill("竖向记录")
        self.page.get_by_role("button", name="发布内容").click()
        self.page.wait_for_load_state("networkidle")

        image = self.page.locator(".media-count-1 [data-entry-image]")
        expect(image).to_be_visible()
        self.assertEqual(
            image.evaluate("element => getComputedStyle(element).objectFit"),
            "contain",
        )
        self.assertLessEqual(image.evaluate("element => element.clientHeight"), 660)
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
        expect(self.page.locator("#id_video_uploads_enabled")).to_be_visible()
        expect(self.page.locator("#id_x_references_enabled")).to_be_visible()
        expect(self.page.locator("#id_youtube_references_enabled")).to_be_visible()
        self.page.screenshot(path=RESULTS_DIR / "admin-configuration-desktop.png", full_page=True)

        SiteConfiguration.objects.filter(pk=1).update(x_references_enabled=False)
        self.page.goto(f"{self.live_server_url}/write/?compose=x")
        expect(self.page.locator('[data-composer-shortcut="x"]')).to_have_count(0)
        expect(self.page.locator('[data-composer-shortcut="text"]')).to_have_attribute(
            "aria-current", "true"
        )
        expect(self.page.locator("#composer-source")).to_be_hidden()
        self.assertEqual(self.page.evaluate("window.scrollY"), 0)
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
        expect(self.page.get_by_role("heading", name="现在保存账号恢复码")).to_be_visible()
        self.assertGreater(len(self.page.locator("#recovery-code-value").input_value()), 20)
        self.page.get_by_role("button", name="我已经安全保存").click()
        self.page.wait_for_load_state("networkidle")
        expect(self.page.get_by_text("恢复码已确认保存", exact=False)).to_be_visible()

        self.page.get_by_role("link", name="写一条").click()
        self.page.get_by_label("正文").fill("邀请制审核闭环：这条内容先进入待审队列。")
        self.page.get_by_role("link", name="图片", exact=True).click()
        self.page.get_by_label("选择图片").set_input_files(
            browser_image_payload(name="review.jpg", color="darkgreen")
        )
        self.page.locator("[data-image-alt]").fill("审核流程配图")
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
        review_image = self.page.locator(".review-media-grid img")
        expect(review_image).to_be_visible()
        self.assertGreater(review_image.evaluate("image => image.naturalWidth"), 0)
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
        self.page.screenshot(path=RESULTS_DIR / "registration-open-mobile.png", full_page=True)
        self.page.get_by_label("用户名").fill("mobile")
        self.page.get_by_label("邮箱").fill("mobile@example.test")
        self.page.get_by_label("密码", exact=True).fill(PASSWORD)
        self.page.get_by_label("确认密码").fill(PASSWORD)
        self.page.get_by_label("我愿意遵守社区公约").check()
        self.page.get_by_role("button", name="加入社区").click()
        self.page.wait_for_load_state("networkidle")

        expect(self.page.get_by_role("heading", name="现在保存账号恢复码")).to_be_visible()
        expect(self.page.locator("#recovery-code-value")).not_to_have_value("")
        self.page.get_by_role("button", name="我已经安全保存").click()
        self.page.wait_for_load_state("networkidle")
        expect(self.page.get_by_text("恢复码已确认保存", exact=False)).to_be_visible()
        menu = self.page.get_by_label("打开导航")
        expect(menu).to_be_visible()
        menu.click()
        expect(self.page.get_by_role("link", name=re.compile("通知"))).to_be_visible()
        expect(self.page.locator("#mobile-discovery")).to_be_visible()
        expect(self.page.locator("#mobile-search")).to_be_visible()
        self.assert_browser_clean()

    def test_member_can_share_an_attributed_x_reference_without_remote_media_download(self):
        self.login("lin")
        self.page.set_viewport_size({"width": 390, "height": 844})
        self.page.get_by_role("link", name="分享 X 来源").click()
        self.page.wait_for_load_state("networkidle")
        source_input = self.page.get_by_label("分享 X / YouTube 来源")
        expect(source_input).not_to_be_focused()
        self.assertEqual(self.page.evaluate("window.scrollY"), 0)
        expect(self.page.locator("#composer-images")).to_be_hidden()
        expect(self.page.locator("#composer-video")).to_be_hidden()
        source_input.fill("https://x.com/")
        expect(self.page.locator("[data-source-status]")).to_contain_text("尚未识别")
        expect(self.page.locator("[data-source-status]")).to_have_class(re.compile("has-error"))
        source_input.fill("https://x.com/i/status/20")
        expect(self.page.get_by_text("已识别为 X Post", exact=False)).to_be_visible()
        self.page.evaluate("window.scrollTo(0, 0)")
        self.page.screenshot(path=RESULTS_DIR / "composer-source-mobile.png", full_page=True)
        with patch(
            "meppp.web.views.refresh_external_reference",
            side_effect=lambda reference: reference,
        ):
            self.page.get_by_role("button", name="发布内容").click()
            self.page.wait_for_load_state("networkidle")

        expect(self.page).to_have_url(re.compile(r"/entry/[0-9a-f-]+/"))
        expect(self.page.get_by_text("分享了一条 X 动态")).to_be_visible()
        source_link = self.page.get_by_role("link", name=re.compile("在 X 查看原文"))
        expect(source_link).to_have_attribute("href", "https://x.com/i/status/20")
        imported = Entry.objects.get(body="分享了一条 X 动态")
        self.assertEqual(imported.attachments.count(), 0)
        self.assertFalse(hasattr(imported, "video"))
        self.assertEqual(imported.external_reference.provider, "x")
        self.assert_browser_clean()

    def test_member_can_publish_a_real_video_only_post_on_mobile(self):
        if not shutil.which("ffmpeg") or not shutil.which("ffprobe"):
            self.skipTest("FFmpeg tools are unavailable")
        try:
            payload = browser_video_payload()
        except RuntimeError as error:
            self.skipTest(f"FFmpeg cannot create the browser fixture: {error}")

        self.login("lin")
        self.page.set_viewport_size({"width": 390, "height": 844})
        video_entry = self.page.get_by_role("link", name="发布视频")
        expect(video_entry).to_be_visible()
        video_entry.click()
        self.page.wait_for_load_state("networkidle")
        expect(self.page).to_have_url(re.compile(r"/write/\?compose=video$"))

        video_input = self.page.get_by_label("选择视频")
        video_input.set_input_files(payload)
        expect(self.page.locator("[data-video-preview]")).to_be_visible()
        expect(self.page.locator("[data-video-status]")).to_contain_text("clip.mp4")
        self.page.get_by_role("button", name="移除视频").click()
        expect(self.page.locator("[data-video-preview]")).to_be_hidden()
        video_input.set_input_files(payload)
        self.page.evaluate("window.scrollTo(0, 0)")
        self.page.screenshot(path=RESULTS_DIR / "composer-video-mobile.png", full_page=True)

        self.page.get_by_role("button", name="发布内容").click()
        self.page.wait_for_load_state("networkidle", timeout=30_000)
        expect(self.page).to_have_url(re.compile(r"/entry/[0-9a-f-]+/"))
        published_video = self.page.locator(".entry-video video")
        expect(published_video).to_be_visible()
        self.page.wait_for_function(
            "document.querySelector('.entry-video video')?.readyState >= 1",
            timeout=15_000,
        )
        entry = Entry.objects.get(author=self.author, body="")
        self.assertTrue(hasattr(entry, "video"))
        self.assert_browser_clean()

    def test_member_can_share_a_youtube_source_with_recognition_and_official_embed(self):
        def mark_ready(reference):
            reference.title = "浏览器验证 YouTube 来源"
            reference.author_name = "MEPPP 测试频道"
            reference.metadata_status = MetadataStatus.READY
            reference.save(update_fields=("title", "author_name", "metadata_status", "updated_at"))
            return reference

        self.context.route(
            "https://www.youtube-nocookie.com/**",
            lambda route: route.fulfill(
                status=200,
                content_type="text/html",
                body="<!doctype html>",
            ),
        )
        self.login("lin")
        self.page.set_viewport_size({"width": 390, "height": 844})
        self.page.get_by_role("link", name="分享 YouTube 来源").click()
        self.page.wait_for_load_state("networkidle")
        source_input = self.page.get_by_label("分享 X / YouTube 来源")
        source_input.fill("https://youtu.be/dQw4w9WgXcQ")
        expect(self.page.get_by_text("已识别为 YouTube 视频", exact=False)).to_be_visible()

        with patch("meppp.web.views.refresh_external_reference", side_effect=mark_ready):
            self.page.get_by_role("button", name="发布内容").click()
            self.page.wait_for_load_state("networkidle")

        expect(self.page.get_by_text("浏览器验证 YouTube 来源")).to_be_visible()
        expect(self.page.locator(".youtube-embed iframe")).to_have_attribute(
            "src",
            "https://www.youtube-nocookie.com/embed/dQw4w9WgXcQ",
        )
        expect(
            self.page.get_by_role("link", name=re.compile("在 YouTube 查看原文"))
        ).to_have_attribute("href", "https://www.youtube.com/watch?v=dQw4w9WgXcQ")
        self.assert_browser_clean()
